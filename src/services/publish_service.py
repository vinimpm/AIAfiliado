"""Publish Service -- publishes videos to TikTok, Instagram, and YouTube.

Responsibilities:
    - Schedule publications at optimal times per platform.
    - Enforce cooldown and daily post limits from the Account Health gate.
    - Upload / publish videos via TikTok Content Posting API, Instagram Graph
      API (Reels), and YouTube Data API v3 (resumable upload).
    - Track publication lifecycle: SCHEDULED -> UPLOADING -> POSTED | FAILED.
    - Generate S3 pre-signed URLs for platforms that ingest via URL.
    - Retry transient errors (429, 5xx) with exponential backoff.
"""

from __future__ import annotations

import random
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import boto3
import httpx
from sqlalchemy import func, select
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.celery_app import celery
from app.config import settings
from app.logging import get_logger
from models.daily_run import DailyRun
from models.database import get_session_cm
from models.publication import Publication
from models.video import Video

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = get_logger(service="publish_service")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=60.0, pool=5.0)

_TIKTOK_API_BASE = "https://open.tiktokapis.com"
_INSTAGRAM_API_BASE = "https://graph.facebook.com/v19.0"
_YOUTUBE_API_BASE = "https://www.googleapis.com"

# Optimal posting times per platform (hour, minute) in America/Sao_Paulo.
# A random variation of 0-30 minutes is added at scheduling time.
SCHEDULE_WINDOWS: dict[str, list[tuple[int, int]]] = {
    "tiktok": [(12, 0), (18, 0), (21, 0)],
    "instagram": [(11, 0), (17, 0), (20, 0)],
    "youtube": [(10, 0), (15, 0), (19, 0)],
}

# Polling parameters for container / upload status checks
_POLL_INTERVAL_SECONDS = 15
_POLL_TIMEOUT_SECONDS = 600  # 10 minutes

# Pre-signed URL TTL for Instagram Reels ingestion
_PRESIGNED_URL_TTL_SECONDS = 3600  # 1 hour


# ---------------------------------------------------------------------------
# Retry predicate -- retry on 429 (rate-limit) and 5xx (server errors)
# ---------------------------------------------------------------------------
def _is_retryable_error(exc: BaseException) -> bool:
    """Return True for HTTP 429 or 5xx errors and connection-level failures.

    HTTP 400-level errors (except 429) are NOT retried because they indicate
    a client-side problem that will not resolve on its own.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout)):
        return True
    return False


# ===========================================================================
# 1. schedule_publication (main Celery task)
# ===========================================================================
@celery.task(
    name="services.publish_service.schedule_publication",
    bind=True,
    max_retries=3,
    acks_late=True,
)
def schedule_publication(
    self,
    video_id: int,
    platform: str,
    scheduled_at: str | None = None,
) -> dict:
    """Schedule and publish a video to a social-media platform.

    Workflow:
        1. Load the Video from the database and verify status is 'ready'.
        2. Pick an optimal schedule time (or use the one provided).
        3. Create a Publication record with status='SCHEDULED'.
        4. Enforce cooldown / daily-post limits from Account Health.
        5. Wait until the scheduled time (or publish immediately if past).
        6. Call the platform-specific publisher.
        7. Update the Publication record (POSTED or FAILED).

    Parameters
    ----------
    video_id : int
        The ID of the Video record to publish.
    platform : str
        Target platform: ``tiktok``, ``instagram``, or ``youtube``.
    scheduled_at : str | None
        ISO-formatted datetime string.  If *None*, an optimal time is chosen
        automatically from ``SCHEDULE_WINDOWS``.

    Returns
    -------
    dict
        Keys: ``publication_id``, ``platform``, ``status``, ``scheduled_at``,
        ``external_id``.
    """
    logger.info(
        "schedule_publication.start",
        video_id=video_id,
        platform=platform,
        scheduled_at=scheduled_at,
    )

    platform = platform.lower().strip()
    if platform not in SCHEDULE_WINDOWS:
        raise ValueError(
            f"Unsupported platform '{platform}'. Supported: {list(SCHEDULE_WINDOWS.keys())}"
        )

    publication_id: int | None = None

    try:
        # ------------------------------------------------------------------
        # Step 1: Load and validate the video
        # ------------------------------------------------------------------
        with get_session_cm() as session:
            video = session.execute(select(Video).where(Video.id == video_id)).scalar_one_or_none()

            if video is None:
                raise ValueError(f"Video {video_id} not found")

            if video.status != "ready":
                raise ValueError(f"Video {video_id} has status '{video.status}', expected 'ready'")

            if not video.s3_url:
                raise ValueError(f"Video {video_id} has no s3_url")

            # ------------------------------------------------------------------
            # Step 2: Determine schedule time
            # ------------------------------------------------------------------
            if scheduled_at is not None:
                publish_at = datetime.fromisoformat(scheduled_at)
                if publish_at.tzinfo is None:
                    publish_at = publish_at.replace(tzinfo=UTC)
            else:
                publish_at = _pick_schedule_time(platform)

            logger.info(
                "schedule_publication.time_picked",
                video_id=video_id,
                platform=platform,
                publish_at=publish_at.isoformat(),
            )

            # ------------------------------------------------------------------
            # Step 3: Check cooldown / daily-post limits
            # ------------------------------------------------------------------
            if not _check_cooldown(platform, session):
                raise RuntimeError(
                    f"Cooldown or daily post limit reached for platform "
                    f"'{platform}'. Publication skipped."
                )

            # ------------------------------------------------------------------
            # Step 4: Create Publication record
            # ------------------------------------------------------------------
            # Find the latest DailyRun to associate
            daily_run = session.execute(
                select(DailyRun).order_by(DailyRun.run_date.desc())
            ).scalar_one_or_none()

            publication = Publication(
                video_id=video.id,
                run_id=daily_run.id if daily_run else None,
                platform=platform,
                status="SCHEDULED",
                scheduled_at=publish_at,
            )
            session.add(publication)
            session.flush()
            publication_id = publication.id

            # Capture fields we need outside the session
            video_s3_url = video.s3_url
            video_script = video.script

            # Build a lightweight video-info dict for publishers
            video_info: dict[str, Any] = {
                "id": video.id,
                "s3_url": video_s3_url,
                "duration": float(video.duration) if video.duration else None,
                "hook": video_script.hook if video_script else "",
                "body": video_script.body if video_script else "",
                "cta": video_script.cta if video_script else "",
                "caption": video_script.caption if video_script else "",
                "affiliate_url": (
                    video_script.product.affiliate_url
                    if video_script and video_script.product
                    else ""
                ),
                "product_title": (
                    video_script.product.title if video_script and video_script.product else ""
                ),
                "category": (
                    video_script.product.category if video_script and video_script.product else ""
                ),
            }

        logger.info(
            "schedule_publication.record_created",
            publication_id=publication_id,
            platform=platform,
            scheduled_at=publish_at.isoformat(),
        )

        # ------------------------------------------------------------------
        # Step 5: Wait until the scheduled time (if in the future)
        # ------------------------------------------------------------------
        now = datetime.now(UTC)
        wait_seconds = (publish_at - now).total_seconds()
        if wait_seconds > 0:
            logger.info(
                "schedule_publication.waiting",
                publication_id=publication_id,
                wait_seconds=round(wait_seconds, 1),
            )
            time.sleep(wait_seconds)

        # ------------------------------------------------------------------
        # Step 6: Update status to UPLOADING
        # ------------------------------------------------------------------
        with get_session_cm() as session:
            publication = session.execute(
                select(Publication).where(Publication.id == publication_id)
            ).scalar_one()
            publication.status = "UPLOADING"

        logger.info(
            "schedule_publication.uploading",
            publication_id=publication_id,
            platform=platform,
        )

        # ------------------------------------------------------------------
        # Step 7: Publish to the platform
        # ------------------------------------------------------------------
        result = _publish_to_platform(platform, publication_id, video_info)

        # ------------------------------------------------------------------
        # Step 8: Update Publication record based on result
        # ------------------------------------------------------------------
        with get_session_cm() as session:
            publication = session.execute(
                select(Publication).where(Publication.id == publication_id)
            ).scalar_one()

            if result["status"] == "POSTED":
                publication.status = "POSTED"
                publication.external_id = result.get("external_id")
                publication.posted_at = datetime.now(UTC)
                publication.error_message = None
            else:
                publication.status = "FAILED"
                publication.external_id = result.get("external_id")
                publication.error_message = result.get("error", "Unknown error")

            final_status = publication.status
            final_external_id = publication.external_id
            final_scheduled_at = (
                publication.scheduled_at.isoformat() if publication.scheduled_at else None
            )

        response = {
            "publication_id": publication_id,
            "platform": platform,
            "status": final_status,
            "scheduled_at": final_scheduled_at,
            "external_id": final_external_id,
        }

        logger.info(
            "schedule_publication.done",
            publication_id=publication_id,
            platform=platform,
            status=final_status,
            external_id=final_external_id,
        )

        return response

    except Exception as exc:
        logger.error(
            "schedule_publication.error",
            video_id=video_id,
            platform=platform,
            publication_id=publication_id,
            error=str(exc),
            exc_info=True,
        )

        # Mark publication as FAILED if the record was created
        if publication_id is not None:
            try:
                with get_session_cm() as session:
                    publication = session.execute(
                        select(Publication).where(Publication.id == publication_id)
                    ).scalar_one_or_none()
                    if publication and publication.status != "POSTED":
                        publication.status = "FAILED"
                        publication.error_message = str(exc)[:500]
            except Exception as db_exc:
                logger.error(
                    "schedule_publication.failed_status_update_error",
                    publication_id=publication_id,
                    error=str(db_exc),
                )

        # Retry with Celery backoff: 60s, 300s, 600s
        retry_countdown = [60, 300, 600]
        attempt = self.request.retries
        countdown = retry_countdown[attempt] if attempt < len(retry_countdown) else 600
        raise self.retry(exc=exc, countdown=countdown)


# ===========================================================================
# 2. _pick_schedule_time
# ===========================================================================
def _pick_schedule_time(platform: str) -> datetime:
    """Pick a random optimal posting time for *platform*.

    Selects a random slot from ``SCHEDULE_WINDOWS[platform]`` that has not
    yet passed today.  If all slots have already passed, picks the first
    slot for tomorrow.  A random variation of 0-30 minutes is added.

    Parameters
    ----------
    platform : str
        The target platform (``tiktok``, ``instagram``, or ``youtube``).

    Returns
    -------
    datetime
        A timezone-aware (UTC) datetime for the scheduled publication.
    """
    now = datetime.now(UTC)
    windows = SCHEDULE_WINDOWS[platform]

    # Add random 0-30 minute variation to each window
    candidates: list[datetime] = []
    for hour, minute in windows:
        variation_minutes = random.randint(0, 30)
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(
            minutes=variation_minutes
        )
        candidates.append(candidate)

    # Filter to times that are still in the future
    future_candidates = [c for c in candidates if c > now]

    if future_candidates:
        chosen = random.choice(future_candidates)
        logger.info(
            "_pick_schedule_time.chosen_today",
            platform=platform,
            scheduled_at=chosen.isoformat(),
        )
        return chosen

    # All times have passed today -- pick the first slot tomorrow
    first_hour, first_minute = windows[0]
    variation_minutes = random.randint(0, 30)
    tomorrow = now + timedelta(days=1)
    chosen = tomorrow.replace(
        hour=first_hour, minute=first_minute, second=0, microsecond=0
    ) + timedelta(minutes=variation_minutes)

    logger.info(
        "_pick_schedule_time.chosen_tomorrow",
        platform=platform,
        scheduled_at=chosen.isoformat(),
    )
    return chosen


# ===========================================================================
# 3. _publish_to_platform (router)
# ===========================================================================
def _publish_to_platform(
    platform: str,
    publication_id: int,
    video_info: dict[str, Any],
) -> dict:
    """Route publication to the correct platform-specific publisher.

    Parameters
    ----------
    platform : str
        Target platform identifier.
    publication_id : int
        The Publication record ID (for logging).
    video_info : dict
        Video metadata dict with keys: id, s3_url, duration, hook, body,
        cta, caption, affiliate_url, product_title, category.

    Returns
    -------
    dict
        Keys: ``external_id`` (str | None), ``status`` (str),
        and optionally ``error`` (str).
    """
    publishers = {
        "tiktok": _publish_tiktok,
        "instagram": _publish_instagram,
        "youtube": _publish_youtube,
    }

    publisher = publishers.get(platform)
    if publisher is None:
        return {
            "external_id": None,
            "status": "FAILED",
            "error": f"No publisher for platform '{platform}'",
        }

    logger.info(
        "_publish_to_platform.dispatching",
        platform=platform,
        publication_id=publication_id,
        video_id=video_info["id"],
    )

    return publisher(publication_id, video_info)


# ===========================================================================
# 4. _check_cooldown
# ===========================================================================
def _check_cooldown(platform: str, session) -> bool:
    """Check whether the cooldown period has elapsed and the daily post limit
    has not been exceeded.

    Uses the latest ``DailyRun`` record to read ``cooldown_minutes`` and
    ``posts_allowed`` values set by the Account Health gate.

    Parameters
    ----------
    platform : str
        The target platform.
    session : Session
        Active SQLAlchemy session.

    Returns
    -------
    bool
        ``True`` if the publication is allowed, ``False`` otherwise.
    """
    # Fetch the latest DailyRun
    daily_run = session.execute(
        select(DailyRun).order_by(DailyRun.run_date.desc())
    ).scalar_one_or_none()

    if daily_run is None:
        # No DailyRun exists yet; allow publication (first run)
        logger.warning(
            "_check_cooldown.no_daily_run",
            platform=platform,
            decision="allow",
        )
        return True

    # If risk_level is HIGH and posts_allowed is 0, block all
    if daily_run.posts_allowed <= 0:
        logger.warning(
            "_check_cooldown.blocked_by_posts_allowed",
            platform=platform,
            posts_allowed=daily_run.posts_allowed,
            risk_level=daily_run.risk_level,
        )
        return False

    # Count how many posts have been made today across all platforms
    today_start = datetime(
        *date.today().timetuple()[:3],
        tzinfo=UTC,
    )
    posts_today = (
        session.execute(
            select(func.count())
            .select_from(Publication)
            .where(
                Publication.status.in_(["POSTED", "UPLOADING", "SCHEDULED"]),
                Publication.scheduled_at >= today_start,
            )
        ).scalar()
        or 0
    )

    if posts_today >= daily_run.posts_allowed:
        logger.warning(
            "_check_cooldown.daily_limit_reached",
            platform=platform,
            posts_today=posts_today,
            posts_allowed=daily_run.posts_allowed,
        )
        return False

    # Check cooldown: time since last POSTED publication on this platform
    cooldown_minutes = daily_run.cooldown_minutes
    if cooldown_minutes > 0:
        last_posted = session.execute(
            select(func.max(Publication.posted_at)).where(
                Publication.platform == platform,
                Publication.status == "POSTED",
            )
        ).scalar()

        if last_posted is not None:
            elapsed = datetime.now(UTC) - last_posted
            required = timedelta(minutes=cooldown_minutes)
            if elapsed < required:
                remaining = (required - elapsed).total_seconds() / 60
                logger.warning(
                    "_check_cooldown.cooldown_active",
                    platform=platform,
                    cooldown_minutes=cooldown_minutes,
                    elapsed_minutes=round(elapsed.total_seconds() / 60, 1),
                    remaining_minutes=round(remaining, 1),
                )
                return False

    logger.info(
        "_check_cooldown.allowed",
        platform=platform,
        posts_today=posts_today,
        posts_allowed=daily_run.posts_allowed,
        cooldown_minutes=cooldown_minutes,
    )
    return True


# ===========================================================================
# 5. TikTok Publishing
# ===========================================================================
@retry(
    retry=retry_if_exception(_is_retryable_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=60, max=600),
    reraise=True,
)
def _publish_tiktok(publication_id: int, video_info: dict[str, Any]) -> dict:
    """Publish a video to TikTok using the Content Posting API.

    Flow:
        1. Download the video binary from S3.
        2. POST to ``/v2/post/publish/inbox/video/init/`` to initialise the
           upload and obtain an ``upload_url``.
        3. PUT the video binary to the ``upload_url``.
        4. Poll ``/v2/post/publish/status/fetch/`` until the post is live
           or an error occurs.

    Parameters
    ----------
    publication_id : int
        Publication record ID (for logging).
    video_info : dict
        Video metadata dict.

    Returns
    -------
    dict
        Keys: ``external_id``, ``status``, and optionally ``error``.
    """
    logger.info(
        "_publish_tiktok.start",
        publication_id=publication_id,
        video_id=video_info["id"],
    )

    access_token = settings.TIKTOK_ACCESS_TOKEN
    if not access_token:
        return {
            "external_id": None,
            "status": "FAILED",
            "error": "TIKTOK_ACCESS_TOKEN is not configured",
        }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        # ------------------------------------------------------------------
        # Step 1: Download video from S3
        # ------------------------------------------------------------------
        video_bytes = _download_from_s3(video_info["s3_url"])

        logger.info(
            "_publish_tiktok.video_downloaded",
            publication_id=publication_id,
            size_bytes=len(video_bytes),
        )

        # ------------------------------------------------------------------
        # Step 2: Initialise the upload
        # ------------------------------------------------------------------
        # Build the caption from script caption or hook (max 150 chars)
        caption = video_info.get("caption") or video_info.get("hook", "")
        caption = caption[:150]

        init_url = f"{_TIKTOK_API_BASE}/v2/post/publish/inbox/video/init/"
        init_payload = {
            "post_info": {
                "title": caption,
                "privacy_level": "PUBLIC_TO_EVERYONE",
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": len(video_bytes),
                "chunk_size": len(video_bytes),
                "total_chunk_count": 1,
            },
        }

        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            init_response = client.post(init_url, json=init_payload, headers=headers)
            init_response.raise_for_status()
            init_data = init_response.json()

        upload_url = init_data.get("data", {}).get("upload_url", "")
        publish_id = init_data.get("data", {}).get("publish_id", "")

        if not upload_url:
            error_msg = init_data.get("error", {}).get("message", "No upload_url in response")
            return {
                "external_id": None,
                "status": "FAILED",
                "error": f"TikTok init failed: {error_msg}",
            }

        logger.info(
            "_publish_tiktok.init_complete",
            publication_id=publication_id,
            publish_id=publish_id,
        )

        # ------------------------------------------------------------------
        # Step 3: Upload the video binary
        # ------------------------------------------------------------------
        upload_headers = {
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{len(video_bytes) - 1}/{len(video_bytes)}",
        }

        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            upload_response = client.put(upload_url, content=video_bytes, headers=upload_headers)
            upload_response.raise_for_status()

        logger.info(
            "_publish_tiktok.upload_complete",
            publication_id=publication_id,
            publish_id=publish_id,
        )

        # ------------------------------------------------------------------
        # Step 4: Poll for publication status
        # ------------------------------------------------------------------
        status_url = f"{_TIKTOK_API_BASE}/v2/post/publish/status/fetch/"
        external_id = _poll_tiktok_status(
            status_url=status_url,
            publish_id=publish_id,
            headers=headers,
        )

        logger.info(
            "_publish_tiktok.done",
            publication_id=publication_id,
            external_id=external_id,
        )

        return {
            "external_id": external_id or publish_id,
            "status": "POSTED",
        }

    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        error_body = exc.response.text[:500]
        error_msg = f"TikTok API error: HTTP {status_code} - {error_body}"
        logger.error(
            "_publish_tiktok.http_error",
            publication_id=publication_id,
            status_code=status_code,
            error=error_msg,
        )
        # Do not retry 400-level errors (except 429, handled by tenacity)
        if 400 <= status_code < 500 and status_code != 429:
            return {
                "external_id": None,
                "status": "FAILED",
                "error": error_msg,
            }
        raise

    except Exception as exc:
        error_msg = f"TikTok publish error: {exc}"
        logger.error(
            "_publish_tiktok.error",
            publication_id=publication_id,
            error=error_msg,
            exc_info=True,
        )
        return {
            "external_id": None,
            "status": "FAILED",
            "error": error_msg,
        }


def _poll_tiktok_status(
    status_url: str,
    publish_id: str,
    headers: dict,
) -> str | None:
    """Poll TikTok publish status until completion or timeout.

    Parameters
    ----------
    status_url : str
        The TikTok status endpoint URL.
    publish_id : str
        The publish ID returned from the init step.
    headers : dict
        Authorization headers.

    Returns
    -------
    str | None
        The external video ID on TikTok, or None if not available.
    """
    start_time = time.monotonic()
    poll_count = 0

    while True:
        elapsed = time.monotonic() - start_time
        if elapsed >= _POLL_TIMEOUT_SECONDS:
            raise TimeoutError(
                f"TikTok publish {publish_id} did not complete within "
                f"{_POLL_TIMEOUT_SECONDS}s ({poll_count} polls)"
            )

        poll_count += 1

        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                response = client.post(
                    status_url,
                    json={"publish_id": publish_id},
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning(
                "_poll_tiktok_status.poll_error",
                publish_id=publish_id,
                poll_count=poll_count,
                error=str(exc),
            )
            time.sleep(_POLL_INTERVAL_SECONDS)
            continue

        status = data.get("data", {}).get("status", "")

        if status == "PUBLISH_COMPLETE":
            video_id = data.get("data", {}).get("video_id")
            logger.info(
                "_poll_tiktok_status.completed",
                publish_id=publish_id,
                video_id=video_id,
                poll_count=poll_count,
            )
            return video_id

        if status in ("FAILED", "PUBLISH_FAILED"):
            fail_reason = data.get("data", {}).get("fail_reason", "Unknown")
            raise RuntimeError(f"TikTok publish failed: status={status}, reason={fail_reason}")

        logger.debug(
            "_poll_tiktok_status.polling",
            publish_id=publish_id,
            status=status,
            poll_count=poll_count,
        )
        time.sleep(_POLL_INTERVAL_SECONDS)


# ===========================================================================
# 6. Instagram Publishing
# ===========================================================================
@retry(
    retry=retry_if_exception(_is_retryable_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=60, max=600),
    reraise=True,
)
def _publish_instagram(publication_id: int, video_info: dict[str, Any]) -> dict:
    """Publish a video as an Instagram Reel via the Graph API.

    Flow:
        1. Generate an S3 pre-signed URL (1h TTL) so Instagram can fetch
           the video directly.
        2. POST to ``/{ig_user_id}/media`` to create a media container
           (``media_type=REELS``).
        3. Poll the container status until ``FINISHED``.
        4. POST to ``/{ig_user_id}/media_publish`` with the ``creation_id``
           to publish the Reel.

    Parameters
    ----------
    publication_id : int
        Publication record ID (for logging).
    video_info : dict
        Video metadata dict.

    Returns
    -------
    dict
        Keys: ``external_id``, ``status``, and optionally ``error``.
    """
    logger.info(
        "_publish_instagram.start",
        publication_id=publication_id,
        video_id=video_info["id"],
    )

    access_token = settings.INSTAGRAM_ACCESS_TOKEN
    ig_user_id = settings.INSTAGRAM_USER_ID

    if not access_token or not ig_user_id:
        return {
            "external_id": None,
            "status": "FAILED",
            "error": "INSTAGRAM_ACCESS_TOKEN or INSTAGRAM_USER_ID not configured",
        }

    try:
        # ------------------------------------------------------------------
        # Step 1: Generate pre-signed S3 URL
        # ------------------------------------------------------------------
        presigned_url = _generate_presigned_url(video_info["s3_url"])

        logger.info(
            "_publish_instagram.presigned_url_generated",
            publication_id=publication_id,
        )

        # ------------------------------------------------------------------
        # Step 2: Create media container
        # ------------------------------------------------------------------
        # Build caption with hashtags
        caption = video_info.get("caption") or video_info.get("hook", "")
        if video_info.get("affiliate_url"):
            caption = f"{caption}\n\n{video_info['affiliate_url']}"

        create_url = f"{_INSTAGRAM_API_BASE}/{ig_user_id}/media"
        create_params = {
            "media_type": "REELS",
            "video_url": presigned_url,
            "caption": caption,
            "access_token": access_token,
        }

        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            create_response = client.post(create_url, params=create_params)
            create_response.raise_for_status()
            create_data = create_response.json()

        creation_id = create_data.get("id")
        if not creation_id:
            error_msg = create_data.get("error", {}).get("message", "No container ID returned")
            return {
                "external_id": None,
                "status": "FAILED",
                "error": f"Instagram container creation failed: {error_msg}",
            }

        logger.info(
            "_publish_instagram.container_created",
            publication_id=publication_id,
            creation_id=creation_id,
        )

        # ------------------------------------------------------------------
        # Step 3: Poll container status until FINISHED
        # ------------------------------------------------------------------
        _poll_instagram_container(creation_id, access_token)

        # ------------------------------------------------------------------
        # Step 4: Publish the container
        # ------------------------------------------------------------------
        publish_url = f"{_INSTAGRAM_API_BASE}/{ig_user_id}/media_publish"
        publish_params = {
            "creation_id": creation_id,
            "access_token": access_token,
        }

        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            publish_response = client.post(publish_url, params=publish_params)
            publish_response.raise_for_status()
            publish_data = publish_response.json()

        media_id = publish_data.get("id")

        logger.info(
            "_publish_instagram.done",
            publication_id=publication_id,
            media_id=media_id,
        )

        return {
            "external_id": media_id,
            "status": "POSTED",
        }

    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        error_body = exc.response.text[:500]
        error_msg = f"Instagram API error: HTTP {status_code} - {error_body}"
        logger.error(
            "_publish_instagram.http_error",
            publication_id=publication_id,
            status_code=status_code,
            error=error_msg,
        )
        if 400 <= status_code < 500 and status_code != 429:
            return {
                "external_id": None,
                "status": "FAILED",
                "error": error_msg,
            }
        raise

    except Exception as exc:
        error_msg = f"Instagram publish error: {exc}"
        logger.error(
            "_publish_instagram.error",
            publication_id=publication_id,
            error=error_msg,
            exc_info=True,
        )
        return {
            "external_id": None,
            "status": "FAILED",
            "error": error_msg,
        }


def _poll_instagram_container(creation_id: str, access_token: str) -> None:
    """Poll an Instagram media container until its status is FINISHED.

    Parameters
    ----------
    creation_id : str
        The container ID returned from the media creation step.
    access_token : str
        The Instagram Graph API access token.

    Raises
    ------
    RuntimeError
        If the container enters an ERROR state.
    TimeoutError
        If the container does not finish within the timeout.
    """
    start_time = time.monotonic()
    poll_count = 0
    status_url = f"{_INSTAGRAM_API_BASE}/{creation_id}"

    while True:
        elapsed = time.monotonic() - start_time
        if elapsed >= _POLL_TIMEOUT_SECONDS:
            raise TimeoutError(
                f"Instagram container {creation_id} did not finish within "
                f"{_POLL_TIMEOUT_SECONDS}s ({poll_count} polls)"
            )

        poll_count += 1

        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                response = client.get(
                    status_url,
                    params={
                        "fields": "status_code",
                        "access_token": access_token,
                    },
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning(
                "_poll_instagram_container.poll_error",
                creation_id=creation_id,
                poll_count=poll_count,
                error=str(exc),
            )
            time.sleep(_POLL_INTERVAL_SECONDS)
            continue

        status_code = data.get("status_code", "")

        if status_code == "FINISHED":
            logger.info(
                "_poll_instagram_container.finished",
                creation_id=creation_id,
                poll_count=poll_count,
            )
            return

        if status_code == "ERROR":
            raise RuntimeError(f"Instagram container {creation_id} failed with ERROR status")

        logger.debug(
            "_poll_instagram_container.polling",
            creation_id=creation_id,
            status_code=status_code,
            poll_count=poll_count,
        )
        time.sleep(_POLL_INTERVAL_SECONDS)


# ===========================================================================
# 7. YouTube Publishing
# ===========================================================================
@retry(
    retry=retry_if_exception(_is_retryable_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=60, max=600),
    reraise=True,
)
def _publish_youtube(publication_id: int, video_info: dict[str, Any]) -> dict:
    """Upload and publish a video to YouTube via the Data API v3.

    Uses the resumable upload protocol:
        1. Download the video from S3 to a temporary file.
        2. POST to ``/upload/youtube/v3/videos?uploadType=resumable``
           to initiate the upload and get a resumable session URI.
        3. PUT the video bytes to the session URI.

    Metadata includes title (from hook, max 100 chars), description
    (body + CTA + affiliate URL), tags, and categoryId=22 (People & Blogs).

    Parameters
    ----------
    publication_id : int
        Publication record ID (for logging).
    video_info : dict
        Video metadata dict.

    Returns
    -------
    dict
        Keys: ``external_id``, ``status``, and optionally ``error``.
    """
    logger.info(
        "_publish_youtube.start",
        publication_id=publication_id,
        video_id=video_info["id"],
    )

    # Obtain a valid access token via refresh token
    access_token = _get_youtube_access_token()
    if not access_token:
        return {
            "external_id": None,
            "status": "FAILED",
            "error": "Failed to obtain YouTube access token",
        }

    try:
        # ------------------------------------------------------------------
        # Step 1: Download video from S3 to a temp file
        # ------------------------------------------------------------------
        video_bytes = _download_from_s3(video_info["s3_url"])

        logger.info(
            "_publish_youtube.video_downloaded",
            publication_id=publication_id,
            size_bytes=len(video_bytes),
        )

        # ------------------------------------------------------------------
        # Step 2: Build metadata
        # ------------------------------------------------------------------
        title = (video_info.get("hook") or video_info.get("product_title", ""))[:100]

        description_parts = []
        if video_info.get("body"):
            description_parts.append(video_info["body"])
        if video_info.get("cta"):
            description_parts.append(video_info["cta"])
        if video_info.get("affiliate_url"):
            description_parts.append(f"\n{video_info['affiliate_url']}")
        description = "\n\n".join(description_parts)

        tags = []
        if video_info.get("category"):
            tags.append(video_info["category"])
        if video_info.get("product_title"):
            # Extract meaningful tags from the product title
            words = video_info["product_title"].split()[:5]
            tags.extend(words)

        metadata = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": "22",  # People & Blogs
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        # ------------------------------------------------------------------
        # Step 3: Initiate resumable upload
        # ------------------------------------------------------------------
        init_url = (
            f"{_YOUTUBE_API_BASE}/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
        )

        init_headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(len(video_bytes)),
            "X-Upload-Content-Type": "video/mp4",
        }

        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            init_response = client.post(init_url, json=metadata, headers=init_headers)
            init_response.raise_for_status()

        # The Location header contains the resumable session URI
        upload_uri = init_response.headers.get("Location")
        if not upload_uri:
            return {
                "external_id": None,
                "status": "FAILED",
                "error": "YouTube did not return a resumable upload URI",
            }

        logger.info(
            "_publish_youtube.upload_initiated",
            publication_id=publication_id,
        )

        # ------------------------------------------------------------------
        # Step 4: Upload the video data
        # ------------------------------------------------------------------
        upload_headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "video/mp4",
            "Content-Length": str(len(video_bytes)),
        }

        with httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=5.0)
        ) as client:
            upload_response = client.put(upload_uri, content=video_bytes, headers=upload_headers)
            upload_response.raise_for_status()
            upload_data = upload_response.json()

        video_id = upload_data.get("id")

        logger.info(
            "_publish_youtube.done",
            publication_id=publication_id,
            video_id=video_id,
        )

        return {
            "external_id": video_id,
            "status": "POSTED",
        }

    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        error_body = exc.response.text[:500]
        error_msg = f"YouTube API error: HTTP {status_code} - {error_body}"
        logger.error(
            "_publish_youtube.http_error",
            publication_id=publication_id,
            status_code=status_code,
            error=error_msg,
        )
        if 400 <= status_code < 500 and status_code != 429:
            return {
                "external_id": None,
                "status": "FAILED",
                "error": error_msg,
            }
        raise

    except Exception as exc:
        error_msg = f"YouTube publish error: {exc}"
        logger.error(
            "_publish_youtube.error",
            publication_id=publication_id,
            error=error_msg,
            exc_info=True,
        )
        return {
            "external_id": None,
            "status": "FAILED",
            "error": error_msg,
        }


# ===========================================================================
# 8. YouTube OAuth Token Refresh
# ===========================================================================
def _get_youtube_access_token() -> str | None:
    """Obtain a valid YouTube API access token by exchanging the refresh token.

    Uses the Google OAuth2 token endpoint to exchange
    ``settings.YOUTUBE_REFRESH_TOKEN`` for a short-lived access token.

    Returns
    -------
    str | None
        The access token, or None on failure.
    """
    if not settings.YOUTUBE_REFRESH_TOKEN:
        logger.error("_get_youtube_access_token.no_refresh_token")
        return None

    if not settings.YOUTUBE_CLIENT_ID or not settings.YOUTUBE_CLIENT_SECRET:
        logger.error("_get_youtube_access_token.no_client_credentials")
        return None

    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": settings.YOUTUBE_CLIENT_ID,
        "client_secret": settings.YOUTUBE_CLIENT_SECRET,
        "refresh_token": settings.YOUTUBE_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            response = client.post(token_url, data=payload)
            response.raise_for_status()
            data = response.json()

        access_token = data.get("access_token")
        if not access_token:
            logger.error(
                "_get_youtube_access_token.no_token_in_response",
                response_keys=list(data.keys()),
            )
            return None

        logger.info(
            "_get_youtube_access_token.success",
            expires_in=data.get("expires_in"),
        )
        return access_token

    except Exception as exc:
        logger.error(
            "_get_youtube_access_token.error",
            error=str(exc),
            exc_info=True,
        )
        return None


# ===========================================================================
# 9. TikTok Token Refresh (stub)
# ===========================================================================
def _refresh_tiktok_token() -> str:
    """Refresh the TikTok access token using the OAuth2 refresh flow.

    Exchanges the current refresh token for a new access/refresh token pair
    via the TikTok OAuth endpoint.

    Returns
    -------
    str
        The new access token.

    .. note::
        TODO: Implement full TikTok OAuth2 refresh flow.
        This requires:
        - Storing the TikTok refresh token (separate from access token).
        - POST to ``https://open.tiktokapis.com/v2/oauth/token/`` with
          ``grant_type=refresh_token``.
        - Updating both access and refresh tokens in settings / secrets store.
        - Handling token rotation (TikTok refresh tokens are single-use).
    """
    logger.warning("_refresh_tiktok_token.stub_called")

    refresh_url = f"{_TIKTOK_API_BASE}/v2/oauth/token/"
    payload = {
        "client_key": settings.TIKTOK_CLIENT_KEY,
        "client_secret": settings.TIKTOK_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": "",  # TODO: store and retrieve TikTok refresh token
    }

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            response = client.post(refresh_url, json=payload)
            response.raise_for_status()
            data = response.json()

        new_access_token = data.get("data", {}).get("access_token", "")
        if new_access_token:
            # TODO: persist the new token to the secrets store / env
            # settings.TIKTOK_ACCESS_TOKEN = new_access_token
            logger.info("_refresh_tiktok_token.success")
            return new_access_token

        logger.error(
            "_refresh_tiktok_token.no_token_in_response",
            response_data=data,
        )
        return settings.TIKTOK_ACCESS_TOKEN

    except Exception as exc:
        logger.error(
            "_refresh_tiktok_token.error",
            error=str(exc),
            exc_info=True,
        )
        # Fall back to the existing token
        return settings.TIKTOK_ACCESS_TOKEN


# ===========================================================================
# 10. Shared helpers
# ===========================================================================
def _download_from_s3(s3_url: str) -> bytes:
    """Download a file from S3 given its full URL.

    Parses the S3 URL to extract the bucket name and key, then uses the
    ``boto3`` client to download the object.

    Parameters
    ----------
    s3_url : str
        The full S3 URL (e.g. ``https://bucket.s3.region.amazonaws.com/key``).

    Returns
    -------
    bytes
        The raw file content.
    """
    logger.info("_download_from_s3.start", s3_url=s3_url[:100])

    # Parse S3 URL to extract bucket and key
    parsed = urlparse(s3_url)
    hostname = parsed.hostname or ""

    # Pattern: {bucket}.s3.{region}.amazonaws.com/{key}
    if ".s3." in hostname and ".amazonaws.com" in hostname:
        bucket = hostname.split(".s3.")[0]
        key = parsed.path.lstrip("/")
    else:
        # Fallback: use configured bucket and treat path as key
        bucket = settings.S3_BUCKET_NAME
        key = parsed.path.lstrip("/")

    s3_client = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION,
    )

    response = s3_client.get_object(Bucket=bucket, Key=key)
    video_bytes = response["Body"].read()

    logger.info(
        "_download_from_s3.done",
        bucket=bucket,
        key=key,
        size_bytes=len(video_bytes),
    )

    return video_bytes


def _generate_presigned_url(s3_url: str) -> str:
    """Generate a pre-signed S3 URL for the given object.

    The pre-signed URL allows unauthenticated GET access to the S3 object
    for a limited time (default: 1 hour).

    Parameters
    ----------
    s3_url : str
        The full S3 URL of the object.

    Returns
    -------
    str
        A pre-signed URL with time-limited GET access.
    """
    parsed = urlparse(s3_url)
    hostname = parsed.hostname or ""

    if ".s3." in hostname and ".amazonaws.com" in hostname:
        bucket = hostname.split(".s3.")[0]
        key = parsed.path.lstrip("/")
    else:
        bucket = settings.S3_BUCKET_NAME
        key = parsed.path.lstrip("/")

    s3_client = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION,
    )

    presigned_url = s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=_PRESIGNED_URL_TTL_SECONDS,
    )

    logger.info(
        "_generate_presigned_url.done",
        bucket=bucket,
        key=key,
        ttl_seconds=_PRESIGNED_URL_TTL_SECONDS,
    )

    return presigned_url
