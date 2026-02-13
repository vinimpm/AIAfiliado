"""Analytics Service -- collects metrics, identifies winners, and feeds the
optimization loop.

Responsibilities:
    - Periodically collect social-media and affiliate metrics for every
      recently posted publication.
    - Snapshot metrics into the ``metrics`` table for historical analysis.
    - Identify winning products based on retention and sales thresholds.
    - Determine A/B test winners by comparing CTR across script variants.
    - Detect stale publications that should be paused.
    - Retire products with no recent sales.
    - Feed performance feedback back into trend and product scores so the
      pipeline improves over time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import and_, func, select

from app.celery_app import celery
from app.cloudwatch import emit_metric
from app.config import settings
from app.logging import get_logger
from models.database import get_session_cm
from models.metric import Metric
from models.product import Product
from models.publication import Publication
from models.script import Script
from models.trend import Trend
from models.video import Video

logger = get_logger(service="analytics_service")

# ---------------------------------------------------------------------------
# Reusable HTTP client settings
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)


# ===========================================================================
# 1. collect_all_metrics (Celery periodic task)
# ===========================================================================

@celery.task(
    name="services.analytics_service.collect_all_metrics",
    bind=True,
    max_retries=3,
)
def collect_all_metrics(self) -> dict:
    """Collect metrics for all publications posted within the last 7 days.

    Workflow:
        1. Find all publications with status='POSTED' and posted_at within
           the last 7 days.
        2. For each publication, call :func:`collect_metrics` to snapshot
           current platform and affiliate metrics.
        3. Run :func:`evaluate_winners` to update winner status on products.
        4. Run :func:`retire_stale_products` to deactivate stale products.
        5. Return a summary dict.
    """
    logger.info("collect_all_metrics.start")

    cutoff = datetime.now(UTC) - timedelta(days=7)
    publications_checked = 0
    errors = 0

    try:
        # Step 1 -- Discover recent publications
        with get_session_cm() as session:
            stmt = (
                select(Publication.id)
                .where(
                    and_(
                        Publication.status == "POSTED",
                        Publication.posted_at >= cutoff,
                    )
                )
                .order_by(Publication.posted_at.desc())
            )
            pub_ids = session.execute(stmt).scalars().all()

        logger.info(
            "collect_all_metrics.found_publications",
            count=len(pub_ids),
        )

        # Step 2 -- Collect metrics for each publication
        for pub_id in pub_ids:
            try:
                collect_metrics(pub_id)
                publications_checked += 1
            except Exception as exc:
                errors += 1
                logger.error(
                    "collect_all_metrics.publication_error",
                    publication_id=pub_id,
                    error=str(exc),
                    exc_info=True,
                )

        # Step 3 -- Evaluate winners
        winner_ids = evaluate_winners()

        # Step 4 -- Retire stale products
        retired_count = retire_stale_products()

        summary = {
            "publications_checked": publications_checked,
            "winners_found": len(winner_ids),
            "retired": retired_count,
            "errors": errors,
        }

        # Emit CloudWatch metrics
        emit_metric("ActiveProducts", len(winner_ids))
        emit_metric("TasksFailedPerHour", errors)

        # Emit daily sales and revenue metrics
        try:
            with get_session_cm() as session:
                agg = session.execute(
                    select(
                        func.coalesce(func.sum(Metric.sales), 0),
                        func.coalesce(func.sum(Metric.revenue), 0),
                    ).where(
                        and_(
                            Metric.publication_id.in_(pub_ids),
                            Metric.collected_at >= cutoff,
                        )
                    )
                ).one()
                emit_metric("DailySales", float(agg[0]))
                emit_metric("DailyRevenue", float(agg[1]))
        except Exception:
            logger.warning("collect_all_metrics.sales_revenue_metric_error", exc_info=True)

        logger.info("collect_all_metrics.done", **summary)
        return summary

    except Exception as exc:
        logger.error(
            "collect_all_metrics.error",
            error=str(exc),
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=120)


# ===========================================================================
# 2. collect_metrics (Celery task)
# ===========================================================================

@celery.task(
    name="services.analytics_service.collect_metrics",
    bind=True,
    max_retries=3,
)
def collect_metrics(self, publication_id: int) -> dict:
    """Collect and persist a metrics snapshot for a single publication.

    Steps:
        1. Load the publication (with its video -> script -> product chain).
        2. Call the appropriate platform metrics collector based on
           ``publication.platform``.
        3. Call the affiliate metrics collector for the related product.
        4. Save a new :class:`Metric` record with all gathered data.
        5. Return the metric dict.
    """
    logger.info("collect_metrics.start", publication_id=publication_id)

    try:
        with get_session_cm() as session:
            # Load publication with necessary relationships
            pub = session.execute(
                select(Publication).where(Publication.id == publication_id)
            ).scalar_one_or_none()

            if pub is None:
                logger.warning(
                    "collect_metrics.publication_not_found",
                    publication_id=publication_id,
                )
                return {}

            if pub.status != "POSTED":
                logger.warning(
                    "collect_metrics.not_posted",
                    publication_id=publication_id,
                    status=pub.status,
                )
                return {}

            external_id = pub.external_id
            platform = pub.platform

            # Traverse relationships to get the product
            video = session.execute(
                select(Video).where(Video.id == pub.video_id)
            ).scalar_one_or_none()

            product_id: int | None = None
            if video is not None:
                script = session.execute(
                    select(Script).where(Script.id == video.script_id)
                ).scalar_one_or_none()
                if script is not None:
                    product_id = script.product_id

            # -- Collect platform metrics --
            platform_metrics = _collect_platform_metrics(platform, external_id)

            # -- Collect affiliate metrics --
            affiliate_metrics: dict[str, Any] = {
                "clicks": 0,
                "sales": 0,
                "revenue": 0.0,
            }
            if product_id is not None:
                try:
                    affiliate_metrics = _collect_affiliate_metrics(
                        product_id, session
                    )
                except Exception as aff_exc:
                    logger.error(
                        "collect_metrics.affiliate_error",
                        product_id=product_id,
                        error=str(aff_exc),
                        exc_info=True,
                    )

            # -- Build and persist the Metric record --
            metric = Metric(
                publication_id=publication_id,
                views=platform_metrics.get("views", 0),
                likes=platform_metrics.get("likes", 0),
                comments=platform_metrics.get("comments", 0),
                shares=platform_metrics.get("shares", 0),
                retention_3s=platform_metrics.get("retention_3s"),
                clicks=affiliate_metrics.get("clicks", 0),
                sales=affiliate_metrics.get("sales", 0),
                revenue=affiliate_metrics.get("revenue", 0.0),
            )
            session.add(metric)
            session.flush()

            metric_dict = {
                "id": metric.id,
                "publication_id": metric.publication_id,
                "views": metric.views,
                "likes": metric.likes,
                "comments": metric.comments,
                "shares": metric.shares,
                "retention_3s": (
                    float(metric.retention_3s)
                    if metric.retention_3s is not None
                    else None
                ),
                "clicks": metric.clicks,
                "sales": metric.sales,
                "revenue": float(metric.revenue),
                "collected_at": metric.collected_at.isoformat()
                if metric.collected_at
                else None,
            }

            logger.info(
                "collect_metrics.done",
                publication_id=publication_id,
                views=metric.views,
                sales=metric.sales,
            )
            return metric_dict

    except Exception as exc:
        logger.error(
            "collect_metrics.error",
            publication_id=publication_id,
            error=str(exc),
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=60)


# ===========================================================================
# 3. Platform metrics collection (stubs with structure)
# ===========================================================================

def _collect_platform_metrics(
    platform: str, external_id: str | None
) -> dict[str, Any]:
    """Route to the correct platform metrics collector."""
    if not external_id:
        logger.warning(
            "_collect_platform_metrics.no_external_id",
            platform=platform,
        )
        return {"views": 0, "likes": 0, "comments": 0, "shares": 0}

    collectors = {
        "tiktok": _collect_tiktok_metrics,
        "instagram": _collect_instagram_metrics,
        "youtube": _collect_youtube_metrics,
    }

    collector = collectors.get(platform)
    if collector is None:
        logger.warning(
            "_collect_platform_metrics.unknown_platform",
            platform=platform,
        )
        return {"views": 0, "likes": 0, "comments": 0, "shares": 0}

    return collector(external_id)


def _collect_tiktok_metrics(external_id: str) -> dict[str, Any]:
    """Collect metrics for a TikTok video via the TikTok Research API.

    Endpoint: GET /v2/video/query/ with video_id parameter.

    Returns:
        dict with keys: views, likes, comments, shares, retention_3s.
    """
    # TODO: Implement TikTok Research API integration.
    #       Use settings.TIKTOK_ACCESS_TOKEN for auth.
    #       Request URL: https://open.tiktokapis.com/v2/video/query/
    #       Headers: {"Authorization": f"Bearer {settings.TIKTOK_ACCESS_TOKEN}"}
    #       Body: {"filters": {"video_ids": [external_id]},
    #              "fields": ["view_count", "like_count", "comment_count",
    #                         "share_count"]}
    #
    #       Parse response and compute retention_3s from the video
    #       analytics endpoint if available.
    #
    # Example implementation:
    #   async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
    #       resp = await client.post(
    #           "https://open.tiktokapis.com/v2/video/query/",
    #           headers={"Authorization": f"Bearer {settings.TIKTOK_ACCESS_TOKEN}"},
    #           json={"filters": {"video_ids": [external_id]},
    #                 "fields": ["view_count", "like_count",
    #                            "comment_count", "share_count"]},
    #       )
    #       resp.raise_for_status()
    #       data = resp.json()["data"]["videos"][0]
    #       return {
    #           "views": data["view_count"],
    #           "likes": data["like_count"],
    #           "comments": data["comment_count"],
    #           "shares": data["share_count"],
    #           "retention_3s": None,  # requires separate analytics call
    #       }

    logger.debug(
        "_collect_tiktok_metrics.stub",
        external_id=external_id,
    )
    return {
        "views": 0,
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "retention_3s": None,
    }


def _collect_instagram_metrics(external_id: str) -> dict[str, Any]:
    """Collect metrics for an Instagram Reel via the Instagram Graph API.

    Endpoint: GET /{media_id}/insights?metric=plays,likes,comments,shares,reach

    Returns:
        dict with keys: views, likes, comments, shares, retention_3s.
    """
    # TODO: Implement Instagram Graph API integration.
    #       Use settings.INSTAGRAM_ACCESS_TOKEN for auth.
    #       Request URL:
    #           f"https://graph.instagram.com/{external_id}/insights"
    #           f"?metric=plays,likes,comments,shares,reach"
    #           f"&access_token={settings.INSTAGRAM_ACCESS_TOKEN}"
    #
    #       Parse response: each metric is in response["data"] as a list
    #       of {name, period, values: [{value}]} objects.
    #
    # Example implementation:
    #   with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
    #       resp = client.get(
    #           f"https://graph.instagram.com/{external_id}/insights",
    #           params={
    #               "metric": "plays,likes,comments,shares,reach",
    #               "access_token": settings.INSTAGRAM_ACCESS_TOKEN,
    #           },
    #       )
    #       resp.raise_for_status()
    #       insights = {
    #           item["name"]: item["values"][0]["value"]
    #           for item in resp.json()["data"]
    #       }
    #       return {
    #           "views": insights.get("plays", 0),
    #           "likes": insights.get("likes", 0),
    #           "comments": insights.get("comments", 0),
    #           "shares": insights.get("shares", 0),
    #           "retention_3s": None,
    #       }

    logger.debug(
        "_collect_instagram_metrics.stub",
        external_id=external_id,
    )
    return {
        "views": 0,
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "retention_3s": None,
    }


def _collect_youtube_metrics(external_id: str) -> dict[str, Any]:
    """Collect metrics for a YouTube Short via the YouTube Analytics API.

    Endpoint: YouTube Data API v3 /videos?part=statistics&id={external_id}

    Returns:
        dict with keys: views, likes, comments, shares, retention_3s.
    """
    # TODO: Implement YouTube Data API v3 + Analytics API integration.
    #       Use settings.YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET /
    #       YOUTUBE_REFRESH_TOKEN for OAuth2 token refresh.
    #
    #       Step 1 -- Get basic stats via Data API:
    #           GET https://www.googleapis.com/youtube/v3/videos
    #               ?part=statistics&id={external_id}
    #
    #       Step 2 -- (Optional) Get retention data via Analytics API:
    #           GET https://youtubeanalytics.googleapis.com/v2/reports
    #               ?ids=channel==MINE
    #               &startDate=...&endDate=...
    #               &metrics=averageViewDuration,views
    #               &filters=video=={external_id}
    #
    # Example implementation:
    #   with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
    #       resp = client.get(
    #           "https://www.googleapis.com/youtube/v3/videos",
    #           params={
    #               "part": "statistics",
    #               "id": external_id,
    #               "key": api_key,  # or use OAuth2 bearer token
    #           },
    #       )
    #       resp.raise_for_status()
    #       stats = resp.json()["items"][0]["statistics"]
    #       return {
    #           "views": int(stats.get("viewCount", 0)),
    #           "likes": int(stats.get("likeCount", 0)),
    #           "comments": int(stats.get("commentCount", 0)),
    #           "shares": 0,  # not available from Data API
    #           "retention_3s": None,
    #       }

    logger.debug(
        "_collect_youtube_metrics.stub",
        external_id=external_id,
    )
    return {
        "views": 0,
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "retention_3s": None,
    }


# ===========================================================================
# 4. Affiliate metrics collection
# ===========================================================================

def _collect_affiliate_metrics(product_id: int, session) -> dict[str, Any]:
    """Collect affiliate metrics (clicks, sales, revenue) for a product.

    Routes to the correct affiliate platform API based on the product's
    ``source_platform``.

    Returns:
        dict with keys: clicks, sales, revenue.
    """
    product = session.execute(
        select(Product).where(Product.id == product_id)
    ).scalar_one_or_none()

    if product is None:
        logger.warning(
            "_collect_affiliate_metrics.product_not_found",
            product_id=product_id,
        )
        return {"clicks": 0, "sales": 0, "revenue": 0.0}

    platform = product.source_platform
    affiliate_collectors = {
        "tiktok_shop": _collect_tiktok_shop_affiliate,
        "hotmart": _collect_hotmart_affiliate,
        "monetizze": _collect_monetizze_affiliate,
        "amazon": _collect_amazon_affiliate,
        "shopee": _collect_shopee_affiliate,
    }

    collector = affiliate_collectors.get(platform)
    if collector is None:
        logger.warning(
            "_collect_affiliate_metrics.unknown_platform",
            product_id=product_id,
            platform=platform,
        )
        return {"clicks": 0, "sales": 0, "revenue": 0.0}

    return collector(product)


def _collect_tiktok_shop_affiliate(product: Product) -> dict[str, Any]:
    """Collect affiliate conversion metrics from TikTok Shop.

    Returns:
        dict with keys: clicks, sales, revenue.
    """
    # TODO: Implement TikTok Shop affiliate reporting API.
    #       Use settings.TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET for auth.
    #       Endpoint: GET /affiliate/order/list
    #       Filter by product affiliate_url or product external ID.
    logger.debug(
        "_collect_tiktok_shop_affiliate.stub",
        product_id=product.id,
    )
    return {"clicks": 0, "sales": 0, "revenue": 0.0}


def _collect_hotmart_affiliate(product: Product) -> dict[str, Any]:
    """Collect affiliate conversion metrics from Hotmart.

    Returns:
        dict with keys: clicks, sales, revenue.
    """
    # TODO: Implement Hotmart Affiliates API.
    #       Use settings.HOTMART_CLIENT_ID / HOTMART_CLIENT_SECRET /
    #       HOTMART_ACCESS_TOKEN for auth.
    #       Endpoint: GET /payments/api/v1/sales/history
    #       Filter by product code extracted from affiliate_url.
    logger.debug(
        "_collect_hotmart_affiliate.stub",
        product_id=product.id,
    )
    return {"clicks": 0, "sales": 0, "revenue": 0.0}


def _collect_monetizze_affiliate(product: Product) -> dict[str, Any]:
    """Collect affiliate conversion metrics from Monetizze.

    Returns:
        dict with keys: clicks, sales, revenue.
    """
    # TODO: Implement Monetizze reporting API.
    #       Endpoint: GET /api/2.1/transactions
    #       Filter by product ID.
    logger.debug(
        "_collect_monetizze_affiliate.stub",
        product_id=product.id,
    )
    return {"clicks": 0, "sales": 0, "revenue": 0.0}


def _collect_amazon_affiliate(product: Product) -> dict[str, Any]:
    """Collect affiliate conversion metrics from Amazon Associates.

    Returns:
        dict with keys: clicks, sales, revenue.
    """
    # TODO: Implement Amazon Associates reporting API.
    #       Use settings.AMAZON_ACCESS_KEY / AMAZON_SECRET_KEY /
    #       AMAZON_ASSOCIATE_TAG for auth.
    #       Note: Amazon PA-API does not provide real-time conversion data;
    #       consider scraping the Associates Central reports or using the
    #       Amazon Advertising API.
    logger.debug(
        "_collect_amazon_affiliate.stub",
        product_id=product.id,
    )
    return {"clicks": 0, "sales": 0, "revenue": 0.0}


def _collect_shopee_affiliate(product: Product) -> dict[str, Any]:
    """Collect affiliate conversion metrics from Shopee.

    Returns:
        dict with keys: clicks, sales, revenue.
    """
    # TODO: Implement Shopee Affiliate reporting API.
    #       Use settings.SHOPEE_APP_ID / SHOPEE_SECRET for auth.
    #       Endpoint: /api/v2/shop/get_shop_info or affiliate reports endpoint.
    logger.debug(
        "_collect_shopee_affiliate.stub",
        product_id=product.id,
    )
    return {"clicks": 0, "sales": 0, "revenue": 0.0}


# ===========================================================================
# 5. evaluate_winners
# ===========================================================================

def evaluate_winners() -> list[int]:
    """Evaluate active products and identify winners.

    A product is considered a **winner** when at least one of its
    publications has:
        - ``retention_3s > 0.70`` in the latest metric snapshot, AND
        - ``sales > 0`` in any metric recorded within the last
          ``WINNER_SALES_WINDOW_DAYS`` (default: 3 days).

    For each winner, the product's ``is_active`` flag is ensured to be
    ``True`` and ``total_sales`` / ``total_revenue`` are updated with the
    latest aggregate values.

    Returns:
        List of product IDs identified as winners.
    """
    logger.info("evaluate_winners.start")

    sales_cutoff = datetime.now(UTC) - timedelta(
        days=settings.WINNER_SALES_WINDOW_DAYS,
    )
    winner_ids: list[int] = []

    try:
        with get_session_cm() as session:
            # Get all active, validated products
            products = (
                session.execute(
                    select(Product).where(
                        and_(
                            Product.is_active.is_(True),
                            Product.status == "validated",
                        )
                    )
                )
                .scalars()
                .all()
            )

            for product in products:
                # Get all publications for this product via script -> video chain
                pub_ids_stmt = (
                    select(Publication.id)
                    .join(Video, Publication.video_id == Video.id)
                    .join(Script, Video.script_id == Script.id)
                    .where(
                        and_(
                            Script.product_id == product.id,
                            Publication.status == "POSTED",
                        )
                    )
                )
                pub_ids = session.execute(pub_ids_stmt).scalars().all()

                if not pub_ids:
                    continue

                # Check retention: get the latest metric for each publication
                has_good_retention = False
                for pub_id in pub_ids:
                    latest_metric = session.execute(
                        select(Metric)
                        .where(Metric.publication_id == pub_id)
                        .order_by(Metric.collected_at.desc())
                        .limit(1)
                    ).scalar_one_or_none()

                    if (
                        latest_metric is not None
                        and latest_metric.retention_3s is not None
                        and float(latest_metric.retention_3s) > 0.70
                    ):
                        has_good_retention = True
                        break

                if not has_good_retention:
                    continue

                # Check sales: any metric with sales > 0 in the sales window
                has_recent_sales = session.execute(
                    select(func.count(Metric.id)).where(
                        and_(
                            Metric.publication_id.in_(pub_ids),
                            Metric.sales > 0,
                            Metric.collected_at >= sales_cutoff,
                        )
                    )
                ).scalar_one()

                if has_recent_sales == 0:
                    continue

                # This product is a winner -- update aggregates
                agg = session.execute(
                    select(
                        func.coalesce(func.sum(Metric.sales), 0).label(
                            "total_sales"
                        ),
                        func.coalesce(func.sum(Metric.revenue), 0).label(
                            "total_revenue"
                        ),
                    ).where(Metric.publication_id.in_(pub_ids))
                ).one()

                product.is_active = True
                product.total_sales = int(agg.total_sales)
                product.total_revenue = float(agg.total_revenue)
                product.last_used_at = datetime.now(UTC)

                winner_ids.append(product.id)

                logger.info(
                    "evaluate_winners.winner_found",
                    product_id=product.id,
                    title=product.title,
                    total_sales=int(agg.total_sales),
                    total_revenue=float(agg.total_revenue),
                )

        logger.info("evaluate_winners.done", winners=len(winner_ids))
        return winner_ids

    except Exception as exc:
        logger.error(
            "evaluate_winners.error",
            error=str(exc),
            exc_info=True,
        )
        return []


# ===========================================================================
# 6. should_pause
# ===========================================================================

def should_pause(publication_id: int) -> bool:
    """Determine whether a publication should be paused (deprioritised).

    A publication should be paused when its latest metrics show:
        - ``retention_3s < 0.30`` AND more than 24 hours since posting, OR
        - ``views < 100`` AND more than 24 hours since posting.

    Returns:
        ``True`` if the publication should be paused; ``False`` otherwise.
    """
    logger.debug("should_pause.start", publication_id=publication_id)

    try:
        with get_session_cm() as session:
            pub = session.execute(
                select(Publication).where(Publication.id == publication_id)
            ).scalar_one_or_none()

            if pub is None:
                logger.warning(
                    "should_pause.publication_not_found",
                    publication_id=publication_id,
                )
                return False

            # Check hours since posting
            if pub.posted_at is None:
                logger.debug(
                    "should_pause.no_posted_at",
                    publication_id=publication_id,
                )
                return False

            now = datetime.now(UTC)
            hours_since_post = (now - pub.posted_at).total_seconds() / 3600.0

            if hours_since_post <= 24:
                # Too early to judge -- give it time
                return False

            # Get the latest metric snapshot
            latest_metric = session.execute(
                select(Metric)
                .where(Metric.publication_id == publication_id)
                .order_by(Metric.collected_at.desc())
                .limit(1)
            ).scalar_one_or_none()

            if latest_metric is None:
                # No metrics collected yet -- cannot decide
                logger.debug(
                    "should_pause.no_metrics",
                    publication_id=publication_id,
                )
                return False

            retention = (
                float(latest_metric.retention_3s)
                if latest_metric.retention_3s is not None
                else None
            )
            views = latest_metric.views

            # Condition 1: Low retention after 24h
            if retention is not None and retention < 0.30:
                logger.info(
                    "should_pause.low_retention",
                    publication_id=publication_id,
                    retention_3s=retention,
                    hours_since_post=round(hours_since_post, 1),
                )
                return True

            # Condition 2: Very low views after 24h
            if views < 100:
                logger.info(
                    "should_pause.low_views",
                    publication_id=publication_id,
                    views=views,
                    hours_since_post=round(hours_since_post, 1),
                )
                return True

            return False

    except Exception as exc:
        logger.error(
            "should_pause.error",
            publication_id=publication_id,
            error=str(exc),
            exc_info=True,
        )
        # Fail-safe: do not pause on error
        return False


# ===========================================================================
# 7. determine_ab_winner
# ===========================================================================

def determine_ab_winner(
    product_id: int,
    hours_threshold: int = 48,
) -> str | None:
    """Determine the A/B test winner for a product's script variants.

    Compares the CTR (clicks / views) of variant 'A' and variant 'B'
    publications that are at least ``hours_threshold`` hours old.

    Args:
        product_id: The product whose variants to compare.
        hours_threshold: Minimum hours since posting before a comparison
            is valid (default: 48).

    Returns:
        ``'A'`` or ``'B'`` if one variant clearly wins, or ``None`` if
        there is insufficient data or no meaningful difference.
    """
    logger.info(
        "determine_ab_winner.start",
        product_id=product_id,
        hours_threshold=hours_threshold,
    )

    min_posted_at = datetime.now(UTC) - timedelta(
        hours=hours_threshold
    )

    try:
        with get_session_cm() as session:
            # Get publications for each variant that are old enough
            variant_stats: dict[str, dict[str, float]] = {}

            for variant in ("A", "B"):
                stmt = (
                    select(Publication.id)
                    .join(Video, Publication.video_id == Video.id)
                    .join(Script, Video.script_id == Script.id)
                    .where(
                        and_(
                            Script.product_id == product_id,
                            Script.variant == variant,
                            Publication.status == "POSTED",
                            Publication.posted_at <= min_posted_at,
                        )
                    )
                )
                pub_ids = session.execute(stmt).scalars().all()

                if not pub_ids:
                    logger.debug(
                        "determine_ab_winner.no_publications",
                        product_id=product_id,
                        variant=variant,
                    )
                    continue

                # Aggregate latest metrics across all publications for this variant
                total_views = 0
                total_clicks = 0

                for pub_id in pub_ids:
                    latest = session.execute(
                        select(Metric)
                        .where(Metric.publication_id == pub_id)
                        .order_by(Metric.collected_at.desc())
                        .limit(1)
                    ).scalar_one_or_none()

                    if latest is not None:
                        total_views += latest.views
                        total_clicks += latest.clicks

                if total_views > 0:
                    variant_stats[variant] = {
                        "views": float(total_views),
                        "clicks": float(total_clicks),
                        "ctr": total_clicks / total_views,
                    }

            # Need both variants with data to compare
            if "A" not in variant_stats or "B" not in variant_stats:
                logger.info(
                    "determine_ab_winner.insufficient_data",
                    product_id=product_id,
                    variants_with_data=list(variant_stats.keys()),
                )
                return None

            ctr_a = variant_stats["A"]["ctr"]
            ctr_b = variant_stats["B"]["ctr"]

            logger.info(
                "determine_ab_winner.comparison",
                product_id=product_id,
                ctr_a=round(ctr_a, 4),
                ctr_b=round(ctr_b, 4),
            )

            # Require at least a 10% relative difference to declare a winner
            if ctr_a == 0 and ctr_b == 0:
                return None

            if ctr_a > ctr_b:
                winner = "A"
            elif ctr_b > ctr_a:
                winner = "B"
            else:
                # Exact tie
                return None

            logger.info(
                "determine_ab_winner.result",
                product_id=product_id,
                winner=winner,
                ctr_a=round(ctr_a, 4),
                ctr_b=round(ctr_b, 4),
            )
            return winner

    except Exception as exc:
        logger.error(
            "determine_ab_winner.error",
            product_id=product_id,
            error=str(exc),
            exc_info=True,
        )
        return None


# ===========================================================================
# 8. should_retire
# ===========================================================================

def should_retire(product_id: int) -> bool:
    """Check whether a product should be retired due to inactivity.

    A product should be retired when:
        - It is currently active (``is_active=True``), AND
        - It has had **no sales** in the last
          ``settings.RETIRE_AFTER_DAYS_NO_SALES`` days (default: 7).

    Returns:
        ``True`` if the product should be retired; ``False`` otherwise.
    """
    logger.debug("should_retire.start", product_id=product_id)

    cutoff = datetime.now(UTC) - timedelta(
        days=settings.RETIRE_AFTER_DAYS_NO_SALES,
    )

    try:
        with get_session_cm() as session:
            product = session.execute(
                select(Product).where(Product.id == product_id)
            ).scalar_one_or_none()

            if product is None:
                logger.warning(
                    "should_retire.product_not_found",
                    product_id=product_id,
                )
                return False

            if not product.is_active:
                return False

            # Get all publication IDs for this product
            pub_ids = (
                session.execute(
                    select(Publication.id)
                    .join(Video, Publication.video_id == Video.id)
                    .join(Script, Video.script_id == Script.id)
                    .where(Script.product_id == product_id)
                )
                .scalars()
                .all()
            )

            if not pub_ids:
                # Active product with no publications at all -- retire it
                logger.debug(
                    "should_retire.no_publications",
                    product_id=product_id,
                )
                return True

            # Check for any sales in the recent window
            recent_sales = session.execute(
                select(func.coalesce(func.sum(Metric.sales), 0)).where(
                    and_(
                        Metric.publication_id.in_(pub_ids),
                        Metric.collected_at >= cutoff,
                    )
                )
            ).scalar_one()

            should = int(recent_sales) == 0

            logger.debug(
                "should_retire.result",
                product_id=product_id,
                recent_sales=int(recent_sales),
                should_retire=should,
            )
            return should

    except Exception as exc:
        logger.error(
            "should_retire.error",
            product_id=product_id,
            error=str(exc),
            exc_info=True,
        )
        # Fail-safe: do not retire on error
        return False


# ===========================================================================
# 9. retire_stale_products
# ===========================================================================

def retire_stale_products() -> int:
    """Find and retire all products that match the ``should_retire`` criteria.

    Sets ``is_active=False`` and ``status='retired'`` on each stale product.

    Returns:
        The count of retired products.
    """
    logger.info("retire_stale_products.start")

    cutoff = datetime.now(UTC) - timedelta(
        days=settings.RETIRE_AFTER_DAYS_NO_SALES,
    )
    retired_count = 0

    try:
        with get_session_cm() as session:
            # Get all active products
            active_products = (
                session.execute(
                    select(Product).where(Product.is_active.is_(True))
                )
                .scalars()
                .all()
            )

            for product in active_products:
                # Get all publication IDs for this product
                pub_ids = (
                    session.execute(
                        select(Publication.id)
                        .join(Video, Publication.video_id == Video.id)
                        .join(Script, Video.script_id == Script.id)
                        .where(Script.product_id == product.id)
                    )
                    .scalars()
                    .all()
                )

                should_retire_product = False

                if not pub_ids:
                    # Active product with no publications -- retire
                    should_retire_product = True
                else:
                    # Check for any sales in the recent window
                    recent_sales = session.execute(
                        select(func.coalesce(func.sum(Metric.sales), 0)).where(
                            and_(
                                Metric.publication_id.in_(pub_ids),
                                Metric.collected_at >= cutoff,
                            )
                        )
                    ).scalar_one()

                    if int(recent_sales) == 0:
                        should_retire_product = True

                if should_retire_product:
                    product.is_active = False
                    product.status = "retired"
                    retired_count += 1

                    logger.info(
                        "retire_stale_products.retired",
                        product_id=product.id,
                        title=product.title,
                    )

        logger.info("retire_stale_products.done", retired_count=retired_count)
        return retired_count

    except Exception as exc:
        logger.error(
            "retire_stale_products.error",
            error=str(exc),
            exc_info=True,
        )
        return 0


# ===========================================================================
# 10. update_trend_score_with_feedback
# ===========================================================================

def update_trend_score_with_feedback(trend_id: int) -> None:
    """Update a trend's score based on real performance feedback.

    Logic:
        - Get all publications linked to scripts that reference this trend.
        - Compute average retention across the latest metric of each publication.
        - Compute total sales across all metrics.
        - If avg retention > 0.7 AND total_sales > 0: multiply score by 1.1
          (capped at 100).
        - If avg retention < 0.3 AND total_sales == 0: multiply score by 0.7
          (floored at 0).
    """
    logger.info(
        "update_trend_score_with_feedback.start",
        trend_id=trend_id,
    )

    try:
        with get_session_cm() as session:
            trend = session.execute(
                select(Trend).where(Trend.id == trend_id)
            ).scalar_one_or_none()

            if trend is None:
                logger.warning(
                    "update_trend_score_with_feedback.trend_not_found",
                    trend_id=trend_id,
                )
                return

            # Get publication IDs for this trend
            pub_ids = (
                session.execute(
                    select(Publication.id)
                    .join(Video, Publication.video_id == Video.id)
                    .join(Script, Video.script_id == Script.id)
                    .where(
                        and_(
                            Script.trend_id == trend_id,
                            Publication.status == "POSTED",
                        )
                    )
                )
                .scalars()
                .all()
            )

            if not pub_ids:
                logger.debug(
                    "update_trend_score_with_feedback.no_publications",
                    trend_id=trend_id,
                )
                return

            # Collect the latest metric for each publication
            retentions: list[float] = []
            total_sales = 0

            for pub_id in pub_ids:
                latest = session.execute(
                    select(Metric)
                    .where(Metric.publication_id == pub_id)
                    .order_by(Metric.collected_at.desc())
                    .limit(1)
                ).scalar_one_or_none()

                if latest is not None:
                    if latest.retention_3s is not None:
                        retentions.append(float(latest.retention_3s))
                    total_sales += latest.sales

            if not retentions:
                logger.debug(
                    "update_trend_score_with_feedback.no_retention_data",
                    trend_id=trend_id,
                )
                return

            avg_retention = sum(retentions) / len(retentions)
            current_score = float(trend.score)

            logger.info(
                "update_trend_score_with_feedback.metrics",
                trend_id=trend_id,
                avg_retention=round(avg_retention, 4),
                total_sales=total_sales,
                current_score=current_score,
            )

            # Apply feedback multiplier
            if avg_retention > 0.7 and total_sales > 0:
                new_score = min(current_score * 1.1, 100.0)
                trend.score = round(new_score, 2)
                logger.info(
                    "update_trend_score_with_feedback.boosted",
                    trend_id=trend_id,
                    old_score=current_score,
                    new_score=round(new_score, 2),
                )
            elif avg_retention < 0.3 and total_sales == 0:
                new_score = max(current_score * 0.7, 0.0)
                trend.score = round(new_score, 2)
                logger.info(
                    "update_trend_score_with_feedback.penalised",
                    trend_id=trend_id,
                    old_score=current_score,
                    new_score=round(new_score, 2),
                )
            else:
                logger.debug(
                    "update_trend_score_with_feedback.no_change",
                    trend_id=trend_id,
                )

    except Exception as exc:
        logger.error(
            "update_trend_score_with_feedback.error",
            trend_id=trend_id,
            error=str(exc),
            exc_info=True,
        )


# ===========================================================================
# 11. update_product_score_with_feedback
# ===========================================================================

def update_product_score_with_feedback(product_id: int) -> None:
    """Update a product's score based on its conversion rate.

    Logic:
        - Count total videos (via scripts -> videos) for this product.
        - Read total_sales from the product record.
        - Compute ``conversion_rate = total_sales / total_videos``.
        - If conversion_rate > 0.5: multiply score by 1.2 (capped at 100).
        - If conversion_rate == 0: multiply score by 0.8 (floored at 0).
    """
    logger.info(
        "update_product_score_with_feedback.start",
        product_id=product_id,
    )

    try:
        with get_session_cm() as session:
            product = session.execute(
                select(Product).where(Product.id == product_id)
            ).scalar_one_or_none()

            if product is None:
                logger.warning(
                    "update_product_score_with_feedback.product_not_found",
                    product_id=product_id,
                )
                return

            # Count total videos for this product
            total_videos = session.execute(
                select(func.count(Video.id))
                .join(Script, Video.script_id == Script.id)
                .where(Script.product_id == product_id)
            ).scalar_one()

            if total_videos == 0:
                logger.debug(
                    "update_product_score_with_feedback.no_videos",
                    product_id=product_id,
                )
                return

            total_sales = product.total_sales
            current_score = float(product.score) if product.score is not None else 50.0
            conversion_rate = total_sales / total_videos

            logger.info(
                "update_product_score_with_feedback.metrics",
                product_id=product_id,
                total_videos=total_videos,
                total_sales=total_sales,
                conversion_rate=round(conversion_rate, 4),
                current_score=current_score,
            )

            # Apply feedback multiplier
            if conversion_rate > 0.5:
                new_score = min(current_score * 1.2, 100.0)
                product.score = round(new_score, 2)
                logger.info(
                    "update_product_score_with_feedback.boosted",
                    product_id=product_id,
                    old_score=current_score,
                    new_score=round(new_score, 2),
                    conversion_rate=round(conversion_rate, 4),
                )
            elif conversion_rate == 0:
                new_score = max(current_score * 0.8, 0.0)
                product.score = round(new_score, 2)
                logger.info(
                    "update_product_score_with_feedback.penalised",
                    product_id=product_id,
                    old_score=current_score,
                    new_score=round(new_score, 2),
                    conversion_rate=0,
                )
            else:
                logger.debug(
                    "update_product_score_with_feedback.no_change",
                    product_id=product_id,
                    conversion_rate=round(conversion_rate, 4),
                )

    except Exception as exc:
        logger.error(
            "update_product_score_with_feedback.error",
            product_id=product_id,
            error=str(exc),
            exc_info=True,
        )
