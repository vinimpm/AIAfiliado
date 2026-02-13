"""Video Service -- generates avatar videos via HeyGen and manages the full lifecycle.

Responsibilities:
    - Build HeyGen API payloads from approved scripts.
    - Apply random visual variation (background, subtitle, avatar position).
    - Enforce daily budget limits before generating.
    - Submit video generation requests to HeyGen /v2/video/generate.
    - Poll HeyGen for completion status.
    - Download finished videos and upload to S3.
    - Persist Video records with status transitions:
      pending -> generating -> ready | failed.
"""

from __future__ import annotations

import hashlib
import random
import time
from datetime import UTC, date, datetime
from typing import Any

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
from app.cloudwatch import emit_metric
from app.config import settings
from app.logging import get_logger
from models.database import get_session_cm
from models.script import Script
from models.video import Video

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = get_logger(service="video_service")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)

_HEYGEN_HEADERS = {
    "X-Api-Key": settings.HEYGEN_API_KEY,
    "Content-Type": "application/json",
}

_SUBTITLE_POSITIONS: list[str] = ["top", "center", "bottom"]
_SUBTITLE_COLORS: list[str] = ["#FFFFFF", "#FFD700", "#FFB6C1"]
_SUBTITLE_FONTS: list[str] = ["Montserrat", "Poppins", "Inter", "Roboto"]
_AVATAR_POSITIONS: list[str] = ["left", "center", "right"]

# Default polling parameters
_DEFAULT_POLL_TIMEOUT_SECONDS = 1200  # 20 minutes
_DEFAULT_POLL_INTERVAL_SECONDS = 30


# ---------------------------------------------------------------------------
# Retry predicate -- retry on 429 (rate limit) and 5xx (server errors)
# ---------------------------------------------------------------------------
def _is_retryable_error(exc: BaseException) -> bool:
    """Return True if the exception represents a retryable HeyGen API error
    (HTTP 429 or 5xx status codes).
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status >= 500:
            emit_metric("HeyGenTimeouts", 1)
        return status == 429 or status >= 500
    if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout)):
        return True
    return False


# ===========================================================================
# 1. generate_video (main Celery task)
# ===========================================================================
@celery.task(
    name="services.video_service.generate_video",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
)
def generate_video(self, script_id: int) -> dict:
    """Generate a video from an approved script using HeyGen.

    Workflow:
        1. Load the script from the database.
        2. Verify that the daily budget has not been exceeded.
        3. Create a Video record (status='pending').
        4. Build the HeyGen payload with visual variation.
        5. POST to HeyGen /v2/video/generate.
        6. Update record (status='generating', heygen_job_id).
        7. Poll for completion (up to 20 minutes).
        8. Download the finished video and upload to S3.
        9. Update record (status='ready', s3_url, duration, cost_usd).

    Parameters
    ----------
    script_id : int
        The ID of the Script to turn into a video.

    Returns
    -------
    dict
        Keys: video_id, s3_url, duration_seconds, cost_usd, heygen_job_id.
    """
    logger.info("generate_video.start", script_id=script_id)

    video_id: int | None = None

    try:
        # ------------------------------------------------------------------
        # Step 1: Load the script
        # ------------------------------------------------------------------
        with get_session_cm() as session:
            script = session.execute(
                select(Script).where(Script.id == script_id)
            ).scalar_one_or_none()

            if script is None:
                raise ValueError(f"Script {script_id} not found")

            if script.status not in ("approved", "used"):
                raise ValueError(
                    f"Script {script_id} has status '{script.status}', "
                    f"expected 'approved' or 'used'"
                )

            logger.info(
                "generate_video.script_loaded",
                script_id=script.id,
                platform=script.platform,
                variant=script.variant,
            )

            # ------------------------------------------------------------------
            # Step 2: Check daily budget
            # ------------------------------------------------------------------
            if not _check_daily_budget(session):
                raise RuntimeError(
                    f"Daily budget of ${settings.DAILY_BUDGET_USD:.2f} USD exceeded. "
                    "Video generation skipped."
                )

            # ------------------------------------------------------------------
            # Step 3: Create Video record (status='pending')
            # ------------------------------------------------------------------
            video = Video(
                script_id=script.id,
                provider="heygen",
                status="pending",
            )
            session.add(video)
            session.flush()
            video_id = video.id

            logger.info(
                "generate_video.video_created",
                video_id=video_id,
                status="pending",
            )

            # ------------------------------------------------------------------
            # Step 4: Build payload with visual variation
            # ------------------------------------------------------------------
            variation = _get_visual_variation()
            payload = _build_heygen_payload(script, variation)

            logger.info(
                "generate_video.payload_built",
                video_id=video_id,
                variation=variation,
            )

            # Capture script fields before leaving the session

        # ------------------------------------------------------------------
        # Step 5: POST to HeyGen /v2/video/generate
        # ------------------------------------------------------------------
        heygen_response = _submit_heygen_generation(payload)

        heygen_job_id = heygen_response.get("data", {}).get("video_id", "")
        if not heygen_job_id:
            raise RuntimeError(
                f"HeyGen response missing video_id: {heygen_response}"
            )

        logger.info(
            "generate_video.submitted",
            video_id=video_id,
            heygen_job_id=heygen_job_id,
        )

        # ------------------------------------------------------------------
        # Step 6: Update record -> 'generating'
        # ------------------------------------------------------------------
        with get_session_cm() as session:
            video = session.execute(
                select(Video).where(Video.id == video_id)
            ).scalar_one()
            video.status = "generating"
            video.heygen_job_id = heygen_job_id

        logger.info(
            "generate_video.status_updated",
            video_id=video_id,
            status="generating",
        )

        # ------------------------------------------------------------------
        # Step 7: Poll for completion
        # ------------------------------------------------------------------
        status_result = _poll_heygen_status(
            job_id=heygen_job_id,
            timeout_seconds=_DEFAULT_POLL_TIMEOUT_SECONDS,
            poll_interval=_DEFAULT_POLL_INTERVAL_SECONDS,
        )

        heygen_status = status_result.get("data", {}).get("status", "")
        if heygen_status != "completed":
            error_msg = status_result.get("data", {}).get("error", "Unknown error")
            raise RuntimeError(
                f"HeyGen video generation failed: status={heygen_status}, "
                f"error={error_msg}"
            )

        video_url = status_result.get("data", {}).get("video_url", "")
        duration_seconds = status_result.get("data", {}).get("duration", 0.0)

        if not video_url:
            raise RuntimeError(
                f"HeyGen completed but no video_url in response: {status_result}"
            )

        logger.info(
            "generate_video.heygen_completed",
            video_id=video_id,
            heygen_job_id=heygen_job_id,
            duration_seconds=duration_seconds,
        )

        # ------------------------------------------------------------------
        # Step 8: Download and upload to S3
        # ------------------------------------------------------------------
        s3_url = _upload_to_s3(video_url=video_url, video_id=video_id)

        logger.info(
            "generate_video.uploaded_to_s3",
            video_id=video_id,
            s3_url=s3_url,
        )

        # ------------------------------------------------------------------
        # Step 9: Compute hash and estimated cost
        # ------------------------------------------------------------------
        video_hash = _compute_video_hash(video_url)

        # HeyGen pricing: approximate cost based on duration
        # Standard plan ~$0.05/second for avatar videos
        cost_usd = round(float(duration_seconds) * 0.05, 4)

        # ------------------------------------------------------------------
        # Step 10: Update record -> 'ready'
        # ------------------------------------------------------------------
        with get_session_cm() as session:
            video = session.execute(
                select(Video).where(Video.id == video_id)
            ).scalar_one()
            video.status = "ready"
            video.s3_url = s3_url
            video.duration = round(float(duration_seconds), 2)
            video.cost_usd = cost_usd
            video.hash = video_hash

            # Mark the script as used
            script_obj = session.execute(
                select(Script).where(Script.id == script_id)
            ).scalar_one_or_none()
            if script_obj and script_obj.status == "approved":
                script_obj.status = "used"

        result = {
            "video_id": video_id,
            "s3_url": s3_url,
            "duration_seconds": float(duration_seconds),
            "cost_usd": cost_usd,
            "heygen_job_id": heygen_job_id,
        }

        logger.info(
            "generate_video.done",
            video_id=video_id,
            s3_url=s3_url,
            duration_seconds=duration_seconds,
            cost_usd=cost_usd,
        )

        return result

    except Exception as exc:
        logger.error(
            "generate_video.error",
            script_id=script_id,
            video_id=video_id,
            error=str(exc),
            exc_info=True,
        )

        # Update video record to 'failed' if it was created
        if video_id is not None:
            try:
                with get_session_cm() as session:
                    video = session.execute(
                        select(Video).where(Video.id == video_id)
                    ).scalar_one_or_none()
                    if video and video.status != "ready":
                        video.status = "failed"
            except Exception as db_exc:
                logger.error(
                    "generate_video.failed_status_update_error",
                    video_id=video_id,
                    error=str(db_exc),
                )

        raise self.retry(exc=exc, countdown=120)


# ===========================================================================
# 2. _build_heygen_payload
# ===========================================================================
def _build_heygen_payload(script: Script, variation: dict) -> dict:
    """Build the HeyGen /v2/video/generate API payload from a Script and
    visual variation parameters.

    Parameters
    ----------
    script : Script
        The script record containing hook, body, and cta text segments.
    variation : dict
        Visual variation dict returned by ``_get_visual_variation()``.

    Returns
    -------
    dict
        The complete JSON payload for the HeyGen API.
    """
    # Combine script segments into the full narration text
    full_text = f"{script.hook}\n\n{script.body}\n\n{script.cta}"

    payload: dict[str, Any] = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": settings.HEYGEN_AVATAR_ID,
                    "avatar_style": "normal",
                },
                "voice": {
                    "type": "text",
                    "input_text": full_text,
                    "voice_id": settings.HEYGEN_VOICE_ID,
                    "speed": settings.HEYGEN_VOICE_SPEED,
                },
                "background": {
                    "type": "color",
                    "value": variation["background_color"],
                },
            }
        ],
        "dimension": {
            "width": 1080,
            "height": 1920,
        },
        "aspect_ratio": "9:16",
        "test": settings.ENV != "production",
    }

    return payload


# ===========================================================================
# 3. _get_visual_variation
# ===========================================================================
def _get_visual_variation() -> dict:
    """Return a dict of random visual variation parameters.

    Each call produces a different combination of subtitle styling,
    background color, and avatar position to ensure visual diversity
    across generated videos.

    Returns
    -------
    dict
        Keys: subtitle_position, subtitle_color, subtitle_font,
        background_color, avatar_position.
    """
    return {
        "subtitle_position": random.choice(_SUBTITLE_POSITIONS),
        "subtitle_color": random.choice(_SUBTITLE_COLORS),
        "subtitle_font": random.choice(_SUBTITLE_FONTS),
        "background_color": random.choice(settings.HEYGEN_BACKGROUND_COLORS),
        "avatar_position": random.choice(_AVATAR_POSITIONS),
    }


# ===========================================================================
# 4. _upload_to_s3
# ===========================================================================
def _upload_to_s3(video_url: str, video_id: int) -> str:
    """Download a video from the HeyGen URL and upload it to S3.

    The S3 key follows the pattern:
        ``{year}/{month}/{day}/video_{video_id}.mp4``

    Parameters
    ----------
    video_url : str
        The public URL of the rendered video on HeyGen's servers.
    video_id : int
        The internal Video record ID, used in the S3 key.

    Returns
    -------
    str
        The full S3 URL (``https://{bucket}.s3.{region}.amazonaws.com/...``).
    """
    logger.info(
        "_upload_to_s3.start",
        video_id=video_id,
        video_url=video_url[:100],
    )

    # Download the video from HeyGen
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        response = client.get(video_url)
        response.raise_for_status()
        video_bytes = response.content

    logger.info(
        "_upload_to_s3.downloaded",
        video_id=video_id,
        size_bytes=len(video_bytes),
    )

    # Build S3 key: {year}/{month}/{day}/video_{id}.mp4
    today = date.today()
    s3_key = (
        f"{today.year}/{today.month:02d}/{today.day:02d}/"
        f"video_{video_id}.mp4"
    )

    # Upload to S3
    s3_client = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION,
    )

    s3_client.put_object(
        Bucket=settings.S3_BUCKET_NAME,
        Key=s3_key,
        Body=video_bytes,
        ContentType="video/mp4",
    )

    s3_url = (
        f"https://{settings.S3_BUCKET_NAME}.s3.{settings.AWS_REGION}"
        f".amazonaws.com/{s3_key}"
    )

    logger.info(
        "_upload_to_s3.done",
        video_id=video_id,
        s3_url=s3_url,
    )

    return s3_url


# ===========================================================================
# 5. _check_daily_budget
# ===========================================================================
def _check_daily_budget(session) -> bool:
    """Check whether the daily video generation budget has been exceeded.

    Sums ``cost_usd`` for all Video records created today and compares
    against ``settings.DAILY_BUDGET_USD``.

    Parameters
    ----------
    session : Session
        Active SQLAlchemy session.

    Returns
    -------
    bool
        ``True`` if the total cost today is under the daily budget limit,
        ``False`` otherwise.
    """
    today_start = datetime(
        *date.today().timetuple()[:3],
        tzinfo=UTC,
    )

    total_cost = session.execute(
        select(func.coalesce(func.sum(Video.cost_usd), 0.0)).where(
            Video.created_at >= today_start
        )
    ).scalar_one()

    total_cost = float(total_cost)
    under_budget = total_cost < settings.DAILY_BUDGET_USD

    logger.info(
        "_check_daily_budget",
        total_cost_today=total_cost,
        daily_budget=settings.DAILY_BUDGET_USD,
        under_budget=under_budget,
    )

    return under_budget


# ===========================================================================
# 6. _poll_heygen_status
# ===========================================================================
def _poll_heygen_status(
    job_id: str,
    timeout_seconds: int = _DEFAULT_POLL_TIMEOUT_SECONDS,
    poll_interval: int = _DEFAULT_POLL_INTERVAL_SECONDS,
) -> dict:
    """Poll the HeyGen video status endpoint until the video is completed,
    failed, or the timeout is reached.

    Parameters
    ----------
    job_id : str
        The HeyGen video/job ID returned from the generation request.
    timeout_seconds : int
        Maximum time to wait for completion (default: 1200 = 20 minutes).
    poll_interval : int
        Seconds between each poll request (default: 30).

    Returns
    -------
    dict
        The full HeyGen status response when the video reaches a terminal
        state (``completed`` or ``failed``).

    Raises
    ------
    TimeoutError
        If the video does not reach a terminal state within ``timeout_seconds``.
    """
    logger.info(
        "_poll_heygen_status.start",
        job_id=job_id,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
    )

    start_time = time.monotonic()
    poll_count = 0

    while True:
        elapsed = time.monotonic() - start_time
        if elapsed >= timeout_seconds:
            emit_metric("HeyGenTimeouts", 1)
            raise TimeoutError(
                f"HeyGen video {job_id} did not complete within "
                f"{timeout_seconds}s ({poll_count} polls)"
            )

        poll_count += 1
        logger.debug(
            "_poll_heygen_status.polling",
            job_id=job_id,
            poll_count=poll_count,
            elapsed_seconds=round(elapsed, 1),
        )

        try:
            status_response = _fetch_heygen_status(job_id)
        except Exception as exc:
            logger.warning(
                "_poll_heygen_status.poll_error",
                job_id=job_id,
                poll_count=poll_count,
                error=str(exc),
            )
            # On transient poll errors, wait and retry rather than aborting
            time.sleep(poll_interval)
            continue

        status = status_response.get("data", {}).get("status", "")

        if status == "completed":
            logger.info(
                "_poll_heygen_status.completed",
                job_id=job_id,
                poll_count=poll_count,
                elapsed_seconds=round(time.monotonic() - start_time, 1),
            )
            return status_response

        if status == "failed":
            error_msg = status_response.get("data", {}).get("error", "")
            logger.error(
                "_poll_heygen_status.failed",
                job_id=job_id,
                poll_count=poll_count,
                error=error_msg,
            )
            return status_response

        # Still processing -- wait before next poll
        time.sleep(poll_interval)


# ===========================================================================
# 7. HeyGen API calls (with tenacity retry)
# ===========================================================================
@retry(
    retry=retry_if_exception(_is_retryable_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=300, max=900),
    reraise=True,
)
def _submit_heygen_generation(payload: dict) -> dict:
    """Submit a video generation request to HeyGen POST /v2/video/generate.

    Retries up to 3 times with exponential backoff (starting at 300s)
    on 429 and 5xx errors.

    Parameters
    ----------
    payload : dict
        The complete HeyGen API payload.

    Returns
    -------
    dict
        The parsed JSON response from HeyGen.

    Raises
    ------
    httpx.HTTPStatusError
        On non-retryable HTTP errors (4xx other than 429).
    """
    url = f"{settings.HEYGEN_API_BASE_URL}/v2/video/generate"

    logger.info("_submit_heygen_generation.request", url=url)

    with httpx.Client(timeout=_HTTP_TIMEOUT, headers=_HEYGEN_HEADERS) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        result = response.json()

    logger.info(
        "_submit_heygen_generation.response",
        status_code=response.status_code,
        video_id=result.get("data", {}).get("video_id", ""),
    )

    return result


@retry(
    retry=retry_if_exception(_is_retryable_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=300, max=900),
    reraise=True,
)
def _fetch_heygen_status(job_id: str) -> dict:
    """Fetch the current status of a HeyGen video generation job.

    GET /v1/video_status.get?video_id={job_id}

    Retries up to 3 times with exponential backoff (starting at 300s)
    on 429 and 5xx errors.

    Parameters
    ----------
    job_id : str
        The HeyGen video/job ID.

    Returns
    -------
    dict
        The parsed JSON response containing status information.
    """
    url = f"{settings.HEYGEN_API_BASE_URL}/v1/video_status.get"
    params = {"video_id": job_id}

    with httpx.Client(timeout=_HTTP_TIMEOUT, headers=_HEYGEN_HEADERS) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()


# ===========================================================================
# 8. Helpers
# ===========================================================================
def _compute_video_hash(video_url: str) -> str:
    """Download the video and compute a SHA-256 hash of its content.

    This hash is stored in the Video record's ``hash`` column to detect
    duplicate videos and ensure content integrity.

    Parameters
    ----------
    video_url : str
        The URL of the video to hash.

    Returns
    -------
    str
        A 64-character hexadecimal SHA-256 digest.
    """
    sha256 = hashlib.sha256()

    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        with client.stream("GET", video_url) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(chunk_size=8192):
                sha256.update(chunk)

    return sha256.hexdigest()
