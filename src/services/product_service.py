"""Product Service -- validates products and manages affiliate links.

Responsibilities:
    - Retrieve active winner products from the database.
    - Discover new products on affiliate platforms for a given set of trend IDs.
    - Validate products against mandatory commercial criteria.
    - Calculate a composite commercial score (commission, rating, price, relevance).
    - Retire stale products with no recent sales.
    - Enforce weekly publication limits per product.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import and_, func, select
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.celery_app import celery
from app.config import settings
from app.logging import get_logger
from models.database import get_session_cm
from models.product import Product
from models.script import Script
from models.trend import Trend

logger = get_logger(service="product_service")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLATFORM_SEARCHERS: dict[str, Any] = {}  # populated after function definitions

# Reusable HTTP client settings
_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)


# ===========================================================================
# 1. get_active_winners
# ===========================================================================


@celery.task(name="services.product_service.get_active_winners", bind=True, max_retries=3)
def get_active_winners(self) -> list[dict]:
    """Return active, validated products that recorded at least one sale
    within the configured winner sales window (default: 3 days).

    Products are ordered by ``total_sales`` descending so that the hottest
    winners appear first.
    """
    logger.info("get_active_winners.start")

    sales_cutoff = datetime.now(UTC) - timedelta(days=settings.WINNER_SALES_WINDOW_DAYS)

    try:
        with get_session_cm() as session:
            stmt = (
                select(Product)
                .where(
                    and_(
                        Product.is_active.is_(True),
                        Product.status == "validated",
                        Product.total_sales > 0,
                        Product.last_used_at >= sales_cutoff,
                    )
                )
                .order_by(Product.total_sales.desc())
            )

            products = session.execute(stmt).scalars().all()

            result = [
                {
                    "id": p.id,
                    "title": p.title,
                    "source_platform": p.source_platform,
                    "price": float(p.price),
                    "commission": float(p.commission),
                    "affiliate_url": p.affiliate_url,
                    "category": p.category,
                    "score": float(p.score) if p.score is not None else None,
                    "total_sales": p.total_sales,
                    "total_revenue": float(p.total_revenue),
                    "last_used_at": p.last_used_at.isoformat() if p.last_used_at else None,
                }
                for p in products
            ]

        logger.info("get_active_winners.done", count=len(result))
        return result

    except Exception as exc:
        logger.error("get_active_winners.error", error=str(exc), exc_info=True)
        raise self.retry(exc=exc, countdown=60)


# ===========================================================================
# 2. find_new_products
# ===========================================================================


@celery.task(name="services.product_service.find_new_products", bind=True, max_retries=3)
def find_new_products(self, trend_ids: list[int]) -> list[dict]:
    """Search affiliate platforms for products matching the given trend IDs.

    For each trend the search follows ``settings.PLATFORM_PRIORITY`` order.
    Every candidate is validated against mandatory criteria; passing products
    receive a commercial score and are persisted to the database.
    """
    logger.info("find_new_products.start", trend_ids=trend_ids)

    saved_products: list[dict] = []

    try:
        with get_session_cm() as session:
            trends = session.execute(select(Trend).where(Trend.id.in_(trend_ids))).scalars().all()

            if not trends:
                logger.warning("find_new_products.no_trends_found", trend_ids=trend_ids)
                return []

            for trend in trends:
                keyword = trend.name
                category = trend.category

                logger.info(
                    "find_new_products.searching_trend",
                    trend_id=trend.id,
                    keyword=keyword,
                    category=category,
                )

                for platform in settings.PLATFORM_PRIORITY:
                    searcher = PLATFORM_SEARCHERS.get(platform)
                    if searcher is None:
                        logger.warning(
                            "find_new_products.unknown_platform",
                            platform=platform,
                        )
                        continue

                    try:
                        candidates = searcher(keyword, category)
                    except Exception as search_exc:
                        logger.error(
                            "find_new_products.platform_search_error",
                            platform=platform,
                            keyword=keyword,
                            error=str(search_exc),
                            exc_info=True,
                        )
                        continue

                    for raw in candidates:
                        if not _validate_product(raw):
                            logger.debug(
                                "find_new_products.product_rejected",
                                title=raw.get("title", ""),
                                platform=platform,
                            )
                            continue

                        score = _calc_commercial_score(raw, trend)

                        # Check for duplicate by affiliate_url
                        existing = session.execute(
                            select(Product).where(Product.affiliate_url == raw["affiliate_url"])
                        ).scalar_one_or_none()

                        if existing is not None:
                            logger.debug(
                                "find_new_products.duplicate_skipped",
                                affiliate_url=raw["affiliate_url"],
                            )
                            continue

                        product = Product(
                            source=raw.get("source", keyword),
                            source_platform=platform,
                            title=raw["title"],
                            price=raw["price"],
                            commission=raw["commission"],
                            affiliate_url=raw["affiliate_url"],
                            category=raw.get("category", category),
                            status="validated",
                            score=round(score, 2),
                            is_active=True,
                            validated_at=datetime.now(UTC),
                        )
                        session.add(product)
                        session.flush()

                        product_dict = {
                            "id": product.id,
                            "title": product.title,
                            "source_platform": product.source_platform,
                            "price": float(product.price),
                            "commission": float(product.commission),
                            "affiliate_url": product.affiliate_url,
                            "category": product.category,
                            "score": float(product.score) if product.score is not None else None,
                            "status": product.status,
                        }
                        saved_products.append(product_dict)

                        logger.info(
                            "find_new_products.product_saved",
                            product_id=product.id,
                            title=product.title,
                            score=score,
                            platform=platform,
                        )

        logger.info("find_new_products.done", total_saved=len(saved_products))
        return saved_products

    except Exception as exc:
        logger.error("find_new_products.error", error=str(exc), exc_info=True)
        raise self.retry(exc=exc, countdown=60)


# ===========================================================================
# 3. Platform search stubs
# ===========================================================================


@retry(
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
def _search_tiktok_shop(keyword: str, category: str | None) -> list[dict]:
    """Search TikTok Shop for affiliate products matching *keyword*.

    Returns a list of raw product dicts with keys:
        title, price, commission, affiliate_url, rating, category, source,
        available.
    """
    # TODO: Implement TikTok Shop product search via TikTok Affiliate API.
    #       Use settings.TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET for auth.
    #       Endpoint: POST /affiliate/product/search
    logger.debug("_search_tiktok_shop.stub", keyword=keyword, category=category)
    return []


@retry(
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
def _search_amazon(keyword: str, category: str | None) -> list[dict]:
    """Search Amazon Associates for affiliate products matching *keyword*.

    Returns a list of raw product dicts with keys:
        title, price, commission, affiliate_url, rating, category, source,
        available.
    """
    # TODO: Implement Amazon Product Advertising API (PA-API 5.0) search.
    #       Use settings.AMAZON_ACCESS_KEY / AMAZON_SECRET_KEY /
    #       AMAZON_ASSOCIATE_TAG for auth.
    #       Endpoint: SearchItems
    logger.debug("_search_amazon.stub", keyword=keyword, category=category)
    return []


@retry(
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
def _search_shopee(keyword: str, category: str | None) -> list[dict]:
    """Search Shopee Affiliate for products matching *keyword*.

    Returns a list of raw product dicts with keys:
        title, price, commission, affiliate_url, rating, category, source,
        available.
    """
    # TODO: Implement Shopee Open Platform product search.
    #       Use settings.SHOPEE_APP_ID / SHOPEE_SECRET for auth.
    #       Endpoint: /api/v2/product/search_item
    logger.debug("_search_shopee.stub", keyword=keyword, category=category)
    return []


@retry(
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
def _search_hotmart(keyword: str, category: str | None) -> list[dict]:
    """Search Hotmart marketplace for digital products matching *keyword*.

    Returns a list of raw product dicts with keys:
        title, price, commission, affiliate_url, rating, category, source,
        available.
    """
    # TODO: Implement Hotmart API product search.
    #       Use settings.HOTMART_CLIENT_ID / HOTMART_CLIENT_SECRET for auth.
    #       Endpoint: /products/api/v2/search
    logger.debug("_search_hotmart.stub", keyword=keyword, category=category)
    return []


@retry(
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
def _search_monetizze(keyword: str, category: str | None) -> list[dict]:
    """Search Monetizze marketplace for products matching *keyword*.

    Returns a list of raw product dicts with keys:
        title, price, commission, affiliate_url, rating, category, source,
        available.
    """
    # TODO: Implement Monetizze API product search.
    #       Endpoint: /api/2.1/product
    logger.debug("_search_monetizze.stub", keyword=keyword, category=category)
    return []


# Register searchers in priority-lookup dict
PLATFORM_SEARCHERS.update(
    {
        "tiktok_shop": _search_tiktok_shop,
        "amazon": _search_amazon,
        "shopee": _search_shopee,
        "hotmart": _search_hotmart,
        "monetizze": _search_monetizze,
    }
)


# ===========================================================================
# 4. Product validation
# ===========================================================================


def _validate_product(raw: dict) -> bool:
    """Check a raw product dict against mandatory commercial criteria.

    Required keys in *raw*:
        commission (float)  -- absolute BRL value **or** percentage (0-100).
        rating    (float)   -- average user rating (0-5 scale).
        price     (float)   -- unit price in BRL.
        category  (str)     -- product category slug.
        available (bool)    -- whether the product is currently in stock.

    Returns ``True`` when all criteria pass, ``False`` otherwise.
    """
    try:
        commission = float(raw.get("commission", 0))
        rating = float(raw.get("rating", 0))
        price = float(raw.get("price", 0))
        category = (raw.get("category") or "").lower().strip()
        available = raw.get("available", False)

        # 1. Availability
        if not available:
            logger.debug("_validate_product.unavailable", title=raw.get("title"))
            return False

        # 2. Commission -- accept if the absolute value meets the BRL minimum
        #    OR if it meets the percentage minimum (commission stored as % 0-100).
        commission_ok = (
            commission >= settings.MIN_COMMISSION_BRL or commission >= settings.MIN_COMMISSION_PCT
        )
        if not commission_ok:
            logger.debug(
                "_validate_product.low_commission",
                title=raw.get("title"),
                commission=commission,
            )
            return False

        # 3. Rating
        if rating < settings.MIN_RATING:
            logger.debug(
                "_validate_product.low_rating",
                title=raw.get("title"),
                rating=rating,
            )
            return False

        # 4. Price range -- use digital limits for digital platforms, physical
        #    limits otherwise.
        source_platform = (raw.get("source_platform") or "").lower()
        if source_platform in ("hotmart", "monetizze"):
            price_min = settings.PRICE_RANGE_DIGITAL_MIN
            price_max = settings.PRICE_RANGE_DIGITAL_MAX
        else:
            price_min = settings.PRICE_RANGE_PHYSICAL_MIN
            price_max = settings.PRICE_RANGE_PHYSICAL_MAX

        if not (price_min <= price <= price_max):
            logger.debug(
                "_validate_product.price_out_of_range",
                title=raw.get("title"),
                price=price,
                price_min=price_min,
                price_max=price_max,
            )
            return False

        # 5. Allowed category
        if category and category not in settings.ALLOWED_CATEGORIES:
            logger.debug(
                "_validate_product.category_not_allowed",
                title=raw.get("title"),
                category=category,
            )
            return False

        return True

    except (TypeError, ValueError) as exc:
        logger.warning(
            "_validate_product.parse_error",
            title=raw.get("title"),
            error=str(exc),
        )
        return False


# ===========================================================================
# 5. Commercial score
# ===========================================================================


def _calc_commercial_score(raw: dict, trend: Trend) -> float:
    """Calculate a composite commercial score (0-100) for a candidate product.

    Components and weights:
        - comissao_score   (35%) -- commission bracket.
        - avaliacao_score  (20%) -- rating bracket.
        - preco_score      (20%) -- price sweet-spot bracket.
        - relevancia_score (25%) -- TF-IDF cosine similarity between the
          product text and the trend text.
    """
    commission_pct = float(raw.get("commission", 0))
    rating = float(raw.get("rating", 0))
    price = float(raw.get("price", 0))

    # ---- Commission score (35%) ----
    if commission_pct >= 30:
        comissao_score = 100.0
    elif commission_pct >= 20:
        comissao_score = 80.0
    elif commission_pct >= 10:
        comissao_score = 60.0
    elif commission_pct >= 5:
        comissao_score = 40.0
    else:
        comissao_score = 0.0

    # ---- Rating score (20%) ----
    if rating >= 4.8:
        avaliacao_score = 100.0
    elif rating >= 4.5:
        avaliacao_score = 80.0
    elif rating >= 4.3:
        avaliacao_score = 60.0
    else:
        avaliacao_score = 0.0

    # ---- Price score (20%) ----
    if 50 <= price <= 100:
        preco_score = 100.0
    elif 30 <= price <= 150:
        preco_score = 70.0
    elif 150 < price <= 297:
        preco_score = 40.0
    else:
        preco_score = 0.0

    # ---- Relevance score (25%) ----
    relevancia_score = _calc_relevance(raw, trend)

    # ---- Weighted total ----
    total = (
        comissao_score * 0.35
        + avaliacao_score * 0.20
        + preco_score * 0.20
        + relevancia_score * 0.25
    )

    logger.debug(
        "_calc_commercial_score",
        title=raw.get("title"),
        comissao=comissao_score,
        avaliacao=avaliacao_score,
        preco=preco_score,
        relevancia=relevancia_score,
        total=round(total, 2),
    )

    return total


def _calc_relevance(raw: dict, trend: Trend) -> float:
    """Compute cosine similarity between product text and trend text using
    TF-IDF vectorisation.  Returns a score in the range [0, 100].
    """
    product_text = " ".join(filter(None, [raw.get("title", ""), raw.get("category", "")])).strip()
    trend_text = " ".join(filter(None, [trend.name, trend.category or ""])).strip()

    if not product_text or not trend_text:
        return 0.0

    try:
        vectorizer = TfidfVectorizer(
            stop_words=None,  # keep stop-words for short text
            lowercase=True,
            max_features=5000,
        )
        tfidf_matrix = vectorizer.fit_transform([product_text, trend_text])
        sim = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])[0][0]
        return float(sim) * 100.0
    except ValueError:
        # e.g. empty vocabulary after tokenisation
        return 0.0


# ===========================================================================
# 6. retire_stale_products
# ===========================================================================


@celery.task(
    name="services.product_service.retire_stale_products",
    bind=True,
    max_retries=3,
)
def retire_stale_products(self) -> int:
    """Deactivate products that have had no sales within the configured
    ``RETIRE_AFTER_DAYS_NO_SALES`` window (default: 7 days).

    Sets ``is_active = False`` and ``status = 'retired'`` on each stale
    product.

    Returns the count of retired products.
    """
    logger.info("retire_stale_products.start")

    cutoff = datetime.now(UTC) - timedelta(days=settings.RETIRE_AFTER_DAYS_NO_SALES)

    try:
        with get_session_cm() as session:
            stmt = select(Product).where(
                and_(
                    Product.is_active.is_(True),
                    # Products that were never used or whose last usage is
                    # older than the cutoff date.
                    (Product.last_used_at.is_(None)) | (Product.last_used_at < cutoff),
                )
            )

            stale_products = session.execute(stmt).scalars().all()
            count = 0

            for product in stale_products:
                product.is_active = False
                product.status = "retired"
                count += 1

                logger.info(
                    "retire_stale_products.retired",
                    product_id=product.id,
                    title=product.title,
                    last_used_at=(
                        product.last_used_at.isoformat() if product.last_used_at else None
                    ),
                )

        logger.info("retire_stale_products.done", retired_count=count)
        return count

    except Exception as exc:
        logger.error("retire_stale_products.error", error=str(exc), exc_info=True)
        raise self.retry(exc=exc, countdown=60)


# ===========================================================================
# 7. check_weekly_limit
# ===========================================================================


def check_weekly_limit(product_id: int) -> bool:
    """Return ``True`` if the product has **not yet reached** the weekly
    script-generation limit (``settings.MAX_VIDEOS_PER_PRODUCT_WEEK``).

    Counts scripts created for *product_id* during the last 7 days.
    """
    logger.debug("check_weekly_limit.start", product_id=product_id)

    week_ago = datetime.now(UTC) - timedelta(days=7)

    try:
        with get_session_cm() as session:
            count = session.execute(
                select(func.count(Script.id)).where(
                    and_(
                        Script.product_id == product_id,
                        Script.created_at >= week_ago,
                    )
                )
            ).scalar_one()

        under_limit = count < settings.MAX_VIDEOS_PER_PRODUCT_WEEK

        logger.debug(
            "check_weekly_limit.result",
            product_id=product_id,
            scripts_this_week=count,
            limit=settings.MAX_VIDEOS_PER_PRODUCT_WEEK,
            under_limit=under_limit,
        )

        return under_limit

    except Exception:
        logger.error(
            "check_weekly_limit.error",
            product_id=product_id,
            exc_info=True,
        )
        # Fail-safe: deny further scripts when we cannot verify the count.
        return False
