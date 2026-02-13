"""Daily Pipeline Orchestrator -- the main entry point for the AIAfiliado system.

Ties all services together in a sequential pipeline that runs once per day:

    1. Evaluate account health (risk gate).
    2. Retrieve active winner products.
    3. Fill remaining slots with new trends and products.
    4. Generate scripts and videos for each product.
    5. Schedule publications on TikTok.

The pipeline is designed for **graceful degradation**: individual step failures
are logged but do not halt subsequent steps.  Only a catastrophic top-level
failure marks the DailyRun as ``failed``.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select

from app.celery_app import celery
from app.cloudwatch import emit_metric
from app.logging import get_logger, set_correlation_id, set_run_id
from models.daily_run import DailyRun
from models.database import get_session_cm
from models.video import Video
from services.account_health import evaluate_account_health
from services.product_service import check_weekly_limit, find_new_products, get_active_winners
from services.publish_service import schedule_publication
from services.script_service import generate_scripts
from services.trend_engine import collect_trends
from services.video_service import generate_video

logger = get_logger(service="orchestrator")


# ===========================================================================
# Main Celery task
# ===========================================================================

@celery.task(
    name="services.orchestrator.trigger_daily_pipeline",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    acks_late=True,
)
def trigger_daily_pipeline(self) -> dict:
    """Execute the full daily content pipeline.

    This is the single entry point invoked by Celery Beat every day at 09:00
    BRT (see ``celery_app.py``).  It orchestrates health evaluation, product
    selection, script generation, video creation, and publication scheduling.

    Returns
    -------
    dict
        Summary with keys: ``run_id``, ``risk_level``, ``posts_allowed``,
        ``videos_generated``, ``publications_scheduled``, ``status``.
    """

    # ------------------------------------------------------------------
    # Context setup
    # ------------------------------------------------------------------
    correlation_id = set_correlation_id(uuid.uuid4().hex[:12])
    today = date.today()
    pipeline_start = time.monotonic()

    logger.info(
        "pipeline.start",
        correlation_id=correlation_id,
        run_date=today.isoformat(),
    )

    # Accumulators for the summary
    run_id: int | None = None
    risk_level: str = "UNKNOWN"
    posts_allowed: int = 0
    videos_generated: int = 0
    publications_scheduled: int = 0
    status: str = "running"

    try:
        # ==============================================================
        # STEP 1 -- Account Health Evaluation
        # ==============================================================
        step_start = time.monotonic()

        try:
            health_result: dict[str, Any] = evaluate_account_health(today.isoformat())

            run_id = health_result.get("run_id")
            risk_level = health_result.get("risk_level", "HIGH")
            posts_allowed = health_result.get("posts_allowed", 0)

            logger.info(
                "pipeline.step1.health_done",
                run_id=run_id,
                risk_level=risk_level,
                risk_score=health_result.get("risk_score"),
                posts_allowed=posts_allowed,
                duration_s=round(time.monotonic() - step_start, 2),
            )

            if run_id is not None:
                set_run_id(run_id)

            # Gate: if risk is HIGH or no posts are allowed, pause early
            if risk_level == "HIGH" or posts_allowed == 0:
                logger.warning(
                    "pipeline.paused",
                    run_id=run_id,
                    risk_level=risk_level,
                    posts_allowed=posts_allowed,
                    reason="high_risk_or_zero_posts",
                )

                _update_daily_run_status(run_id, "paused")

                return {
                    "run_id": run_id,
                    "risk_level": risk_level,
                    "posts_allowed": posts_allowed,
                    "videos_generated": 0,
                    "publications_scheduled": 0,
                    "status": "paused",
                    "duration_s": round(time.monotonic() - pipeline_start, 2),
                }

        except Exception:
            logger.exception("pipeline.step1.health_error")
            # Fail-safe: assume HIGH risk when health check itself fails
            risk_level = "HIGH"
            posts_allowed = 0

            _update_daily_run_status(run_id, "paused")

            return {
                "run_id": run_id,
                "risk_level": risk_level,
                "posts_allowed": posts_allowed,
                "videos_generated": 0,
                "publications_scheduled": 0,
                "status": "paused",
                "duration_s": round(time.monotonic() - pipeline_start, 2),
            }

        # ==============================================================
        # STEP 2 -- Get Active Winners
        # ==============================================================
        step_start = time.monotonic()
        winners: list[dict] = []

        try:
            winners = get_active_winners()

            logger.info(
                "pipeline.step2.winners_done",
                winner_count=len(winners),
                duration_s=round(time.monotonic() - step_start, 2),
            )

        except Exception:
            logger.exception("pipeline.step2.winners_error")
            # Continue with empty winners -- we can still use new trends

        # ==============================================================
        # STEP 3 -- Fill Remaining Slots with New Trends / Products
        # ==============================================================
        step_start = time.monotonic()
        new_products: list[dict] = []

        # Estimate how many slots winners will consume (at most 1 per winner
        # after weekly-limit filtering, but we count conservatively)
        winner_slots = min(len(winners), posts_allowed)
        remaining_slots = posts_allowed - winner_slots

        try:
            if remaining_slots > 0 and run_id is not None:
                # Collect trends
                trends: list[dict] = collect_trends(run_id)
                trend_ids: list[int] = [t["id"] for t in trends if t.get("id")]

                logger.info(
                    "pipeline.step3.trends_collected",
                    trend_count=len(trends),
                    trend_ids=trend_ids,
                    duration_s=round(time.monotonic() - step_start, 2),
                )

                if trend_ids:
                    product_start = time.monotonic()
                    new_products = find_new_products(trend_ids)

                    logger.info(
                        "pipeline.step3.products_found",
                        product_count=len(new_products),
                        duration_s=round(time.monotonic() - product_start, 2),
                    )
            else:
                logger.info(
                    "pipeline.step3.skipped",
                    reason="no_remaining_slots_or_no_run_id",
                    remaining_slots=remaining_slots,
                    run_id=run_id,
                )

        except Exception:
            logger.exception("pipeline.step3.trends_products_error")
            # Continue -- we can still process winners

        logger.info(
            "pipeline.step3.done",
            winner_count=len(winners),
            new_product_count=len(new_products),
            total_duration_s=round(time.monotonic() - step_start, 2),
        )

        # ==============================================================
        # STEP 4 -- Generate Scripts, Videos, and Schedule Publications
        # ==============================================================
        step_start = time.monotonic()
        slots_used = 0

        # ---- 4a: Process winners first ----
        for product in winners:
            if slots_used >= posts_allowed:
                logger.info(
                    "pipeline.step4.slots_exhausted",
                    phase="winners",
                    slots_used=slots_used,
                    posts_allowed=posts_allowed,
                )
                break

            product_id = product["id"]

            try:
                # Check weekly limit
                if not check_weekly_limit(product_id):
                    logger.info(
                        "pipeline.step4.weekly_limit_reached",
                        product_id=product_id,
                        title=product.get("title", ""),
                    )
                    continue

                # Generate scripts (A/B variants for winners)
                script_start = time.monotonic()
                scripts: list[dict] = generate_scripts(
                    product_id=product_id,
                    trend_id=None,
                    platform="tiktok",
                    variant_count=2,
                )

                logger.info(
                    "pipeline.step4.scripts_generated",
                    product_id=product_id,
                    script_count=len(scripts),
                    phase="winner",
                    duration_s=round(time.monotonic() - script_start, 2),
                )

                # Generate video and publish for each approved script
                for script in scripts:
                    if slots_used >= posts_allowed:
                        break

                    script_id = script["id"]

                    try:
                        video_start = time.monotonic()
                        video_result: dict = generate_video(script_id)

                        video_id = video_result.get("video_id")
                        video_status = video_result.get("s3_url")

                        logger.info(
                            "pipeline.step4.video_generated",
                            script_id=script_id,
                            video_id=video_id,
                            phase="winner",
                            duration_s=round(time.monotonic() - video_start, 2),
                        )

                        if video_id and video_status:
                            # Video is ready -- schedule publication
                            try:
                                pub_start = time.monotonic()
                                pub_result: dict = schedule_publication(
                                    video_id=video_id,
                                    platform="tiktok",
                                )

                                publications_scheduled += 1
                                slots_used += 1
                                videos_generated += 1

                                logger.info(
                                    "pipeline.step4.publication_scheduled",
                                    video_id=video_id,
                                    publication_id=pub_result.get("publication_id"),
                                    platform="tiktok",
                                    phase="winner",
                                    duration_s=round(time.monotonic() - pub_start, 2),
                                )

                            except Exception:
                                logger.exception(
                                    "pipeline.step4.publish_error",
                                    video_id=video_id,
                                    phase="winner",
                                )
                                # Video was generated but publish failed.
                                # Still count the video.
                                videos_generated += 1
                        else:
                            logger.warning(
                                "pipeline.step4.video_not_ready",
                                script_id=script_id,
                                video_result=video_result,
                                phase="winner",
                            )

                    except Exception:
                        logger.exception(
                            "pipeline.step4.video_error",
                            script_id=script_id,
                            phase="winner",
                        )
                        # Individual video failure does not stop the pipeline
                        continue

            except Exception:
                logger.exception(
                    "pipeline.step4.winner_product_error",
                    product_id=product_id,
                )
                continue

        # ---- 4b: Process new trend-based products ----
        for product in new_products:
            if slots_used >= posts_allowed:
                logger.info(
                    "pipeline.step4.slots_exhausted",
                    phase="new_products",
                    slots_used=slots_used,
                    posts_allowed=posts_allowed,
                )
                break

            product_id = product["id"]
            # New products come from find_new_products which does not expose
            # a trend_id directly in the product dict.  We pass None as the
            # trend_id since the script_service can handle None for trend_id
            # when generating scripts for products without a specific trend.
            trend_id = product.get("trend_id")

            try:
                # Check weekly limit
                if not check_weekly_limit(product_id):
                    logger.info(
                        "pipeline.step4.weekly_limit_reached",
                        product_id=product_id,
                        title=product.get("title", ""),
                        phase="new_product",
                    )
                    continue

                # Generate scripts (only 1 variant for new products)
                script_start = time.monotonic()
                scripts = generate_scripts(
                    product_id=product_id,
                    trend_id=trend_id,
                    platform="tiktok",
                    variant_count=2,
                )

                logger.info(
                    "pipeline.step4.scripts_generated",
                    product_id=product_id,
                    script_count=len(scripts),
                    phase="new_product",
                    duration_s=round(time.monotonic() - script_start, 2),
                )

                # For new products, use only the first script variant
                for script in scripts[:1]:
                    if slots_used >= posts_allowed:
                        break

                    script_id = script["id"]

                    try:
                        video_start = time.monotonic()
                        video_result = generate_video(script_id)

                        video_id = video_result.get("video_id")
                        video_status = video_result.get("s3_url")

                        logger.info(
                            "pipeline.step4.video_generated",
                            script_id=script_id,
                            video_id=video_id,
                            phase="new_product",
                            duration_s=round(time.monotonic() - video_start, 2),
                        )

                        if video_id and video_status:
                            try:
                                pub_start = time.monotonic()
                                pub_result = schedule_publication(
                                    video_id=video_id,
                                    platform="tiktok",
                                )

                                publications_scheduled += 1
                                slots_used += 1
                                videos_generated += 1

                                logger.info(
                                    "pipeline.step4.publication_scheduled",
                                    video_id=video_id,
                                    publication_id=pub_result.get("publication_id"),
                                    platform="tiktok",
                                    phase="new_product",
                                    duration_s=round(time.monotonic() - pub_start, 2),
                                )

                            except Exception:
                                logger.exception(
                                    "pipeline.step4.publish_error",
                                    video_id=video_id,
                                    phase="new_product",
                                )
                                videos_generated += 1
                        else:
                            logger.warning(
                                "pipeline.step4.video_not_ready",
                                script_id=script_id,
                                video_result=video_result,
                                phase="new_product",
                            )

                    except Exception:
                        logger.exception(
                            "pipeline.step4.video_error",
                            script_id=script_id,
                            phase="new_product",
                        )
                        continue

            except Exception:
                logger.exception(
                    "pipeline.step4.new_product_error",
                    product_id=product_id,
                )
                continue

        logger.info(
            "pipeline.step4.done",
            slots_used=slots_used,
            videos_generated=videos_generated,
            publications_scheduled=publications_scheduled,
            duration_s=round(time.monotonic() - step_start, 2),
        )

        # ==============================================================
        # STEP 5 -- Finalize DailyRun
        # ==============================================================
        status = "completed"
        _update_daily_run_status(run_id, status)

        total_duration = round(time.monotonic() - pipeline_start, 2)

        summary = {
            "run_id": run_id,
            "risk_level": risk_level,
            "posts_allowed": posts_allowed,
            "videos_generated": videos_generated,
            "publications_scheduled": publications_scheduled,
            "status": status,
            "duration_s": total_duration,
        }

        logger.info("pipeline.completed", **summary)

        # Emit CloudWatch metrics
        emit_metric("VideosGenerated", videos_generated)
        emit_metric("PublicationsPosted", publications_scheduled)
        if posts_allowed > 0:
            emit_metric("SuccessRate", publications_scheduled / posts_allowed * 100, "Percent")
        emit_metric("PipelineFailed", 0)

        # Emit daily cost metric
        try:
            with get_session_cm() as session:
                today_start = datetime(*today.timetuple()[:3], tzinfo=UTC)
                total_cost = float(
                    session.execute(
                        select(func.coalesce(func.sum(Video.cost_usd), 0.0)).where(
                            Video.created_at >= today_start
                        )
                    ).scalar_one()
                )
                emit_metric("DailyCostUSD", total_cost)
        except Exception:
            logger.warning("pipeline.daily_cost_metric_error", exc_info=True)

        return summary

    except Exception:
        # Catastrophic failure -- mark the run as failed
        logger.exception("pipeline.catastrophic_failure", run_id=run_id)

        status = "failed"
        _update_daily_run_status(run_id, status)

        total_duration = round(time.monotonic() - pipeline_start, 2)

        emit_metric("PipelineFailed", 1)

        return {
            "run_id": run_id,
            "risk_level": risk_level,
            "posts_allowed": posts_allowed,
            "videos_generated": videos_generated,
            "publications_scheduled": publications_scheduled,
            "status": status,
            "duration_s": total_duration,
        }


# ===========================================================================
# Helpers
# ===========================================================================

def _update_daily_run_status(run_id: int | None, status: str) -> None:
    """Update the DailyRun record with the given status and finished_at timestamp.

    Silently catches and logs database errors to avoid masking upstream
    exceptions.

    Parameters
    ----------
    run_id : int | None
        The DailyRun record ID.  If ``None`` the function returns immediately.
    status : str
        One of ``'running'``, ``'completed'``, ``'failed'``, ``'paused'``.
    """
    if run_id is None:
        logger.warning(
            "_update_daily_run_status.no_run_id",
            status=status,
        )
        return

    try:
        with get_session_cm() as session:
            run = session.execute(
                select(DailyRun).where(DailyRun.id == run_id)
            ).scalar_one_or_none()

            if run is None:
                logger.error(
                    "_update_daily_run_status.run_not_found",
                    run_id=run_id,
                    status=status,
                )
                return

            run.status = status
            run.finished_at = datetime.now(UTC)

        logger.info(
            "_update_daily_run_status.done",
            run_id=run_id,
            status=status,
        )

    except Exception:
        logger.exception(
            "_update_daily_run_status.error",
            run_id=run_id,
            status=status,
        )
