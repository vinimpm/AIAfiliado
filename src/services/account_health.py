"""Account Health Gate -- evaluates risk before any daily action.

Collects five signals (removals, strikes, reach_drop, similarity, cadence),
calculates a weighted risk score with time-decay, and gates the daily pipeline
by setting posts_allowed and cooldown_minutes accordingly.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.celery_app import celery
from app.config import settings
from app.logging import get_logger
from models.daily_run import DailyRun
from models.database import get_session_cm
from models.metric import Metric
from models.publication import Publication
from models.script import Script

logger = get_logger(service="account_health")


# ---------------------------------------------------------------------------
# Thresholds / lookup tables
# ---------------------------------------------------------------------------

_RISK_LEVELS: list[tuple[int, str]] = [
    (34, "LOW"),
    (64, "MEDIUM"),
    (100, "HIGH"),
]

_POSTS_ALLOWED: dict[str, int] = {
    "LOW": 3,
    "MEDIUM": 1,
    "HIGH": 0,
}

_COOLDOWN_MINUTES: dict[str, int] = {
    "LOW": 120,
    "MEDIUM": 240,
    "HIGH": 0,
}


# ---------------------------------------------------------------------------
# Signal scoring helpers
# ---------------------------------------------------------------------------


def _score_removals(count: int) -> int:
    """Convert removal count to a 0-100 risk component score."""
    if count <= 0:
        return 0
    if count == 1:
        return 40
    if count == 2:
        return 70
    return 100


def _score_strikes(count: int) -> int:
    """Convert strike count to a 0-100 risk component score."""
    if count <= 0:
        return 0
    if count == 1:
        return 60
    return 100


def _score_reach_drop(drop_ratio: float) -> int:
    """Convert reach-drop ratio to a 0-100 risk component score.

    ``drop_ratio`` is defined as ``1 - (avg_recent / avg_older)`` so a value
    of 0.3 means views dropped by 30 %.
    """
    if drop_ratio <= 0.0:
        return 0
    if drop_ratio <= 0.3:
        return 30
    if drop_ratio <= 0.5:
        return 60
    return 100


def _score_similarity(avg_sim: float) -> int:
    """Convert average pairwise cosine-similarity to a 0-100 risk score."""
    if avg_sim <= 0.5:
        return 0
    if avg_sim <= 0.7:
        return 40
    if avg_sim <= 0.85:
        return 70
    return 100


def _score_cadence(posts_7d: int) -> int:
    """Convert 7-day publication count to a 0-100 risk score."""
    if posts_7d <= 7:
        return 0
    if posts_7d <= 14:
        return 30
    if posts_7d <= 21:
        return 60
    return 100


# ---------------------------------------------------------------------------
# Decay
# ---------------------------------------------------------------------------


def _apply_decay(score: float, days_since: float) -> float:
    """Reduce *score* by 10 % for every day since the incident.

    Returns a value clamped to [0, score].
    """
    return max(0.0, score * (1.0 - days_since * 0.1))


# ---------------------------------------------------------------------------
# Signal collectors
# ---------------------------------------------------------------------------


def _count_removals(session: Session, days: int = 30) -> int:
    """Count publications whose status changed in a way that suggests removal.

    A publication is considered "removed" when its status is ``FAILED`` and it
    previously reached ``POSTED`` (indicated by ``posted_at`` being set) within
    the look-back window.
    """
    try:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(func.count())
            .select_from(Publication)
            .where(
                Publication.status == "FAILED",
                Publication.posted_at.isnot(None),
                Publication.posted_at >= cutoff,
            )
        )
        result: int = session.execute(stmt).scalar() or 0
        logger.info("signal.removals", count=result, days=days)
        return result
    except Exception:
        logger.exception("signal.removals.error")
        return 0


def _count_strikes(session: Session) -> int:  # noqa: ARG001
    """Return current strike count.

    This is a placeholder -- platform strike data requires manual API
    integration (TikTok Creator API, Meta Graph API, etc.).  Until that
    integration exists, we default to 0 so the gate remains fail-safe.
    """
    try:
        logger.info("signal.strikes", count=0, note="placeholder")
        return 0
    except Exception:
        logger.exception("signal.strikes.error")
        return 0


def _calc_reach_drop(session: Session) -> float:
    """Compare average views of the last 7 publications vs the last 30.

    Returns ``drop_ratio = 1 - (avg_recent / avg_older)``.  A positive value
    means views decreased; zero or negative means they stayed the same or
    increased.
    """
    try:
        # Sub-query: publications ordered by posted_at DESC, with their view sum
        posted_pubs = (
            select(
                Publication.id.label("pub_id"),
                Publication.posted_at.label("posted_at"),
            )
            .where(
                Publication.status == "POSTED",
                Publication.posted_at.isnot(None),
            )
            .order_by(Publication.posted_at.desc())
            .subquery()
        )

        # Collect view totals per publication via the Metric table
        view_stmt = (
            select(
                posted_pubs.c.pub_id,
                func.coalesce(func.sum(Metric.views), 0).label("total_views"),
            )
            .outerjoin(Metric, Metric.publication_id == posted_pubs.c.pub_id)
            .group_by(posted_pubs.c.pub_id, posted_pubs.c.posted_at)
            .order_by(posted_pubs.c.posted_at.desc())
            .limit(30)
        )

        rows = session.execute(view_stmt).all()
        if len(rows) < 2:
            logger.info("signal.reach_drop", drop_ratio=0.0, note="insufficient_data")
            return 0.0

        recent_rows = rows[:7]
        older_rows = rows[7:30]

        if not older_rows:
            logger.info("signal.reach_drop", drop_ratio=0.0, note="no_older_data")
            return 0.0

        avg_recent = sum(r.total_views for r in recent_rows) / len(recent_rows)
        avg_older = sum(r.total_views for r in older_rows) / len(older_rows)

        if avg_older == 0:
            drop_ratio = 0.0
        else:
            drop_ratio = 1.0 - (avg_recent / avg_older)

        logger.info(
            "signal.reach_drop",
            avg_recent=avg_recent,
            avg_older=avg_older,
            drop_ratio=round(drop_ratio, 4),
        )
        return drop_ratio
    except Exception:
        logger.exception("signal.reach_drop.error")
        return 0.0


def _calc_content_similarity(session: Session) -> float:
    """Average pairwise cosine similarity of the last 5 scripts (TF-IDF).

    Returns a float in [0, 1].  If fewer than 2 scripts exist, returns 0.0.
    """
    try:
        stmt = (
            select(Script.hook, Script.body, Script.cta).order_by(Script.created_at.desc()).limit(5)
        )
        rows = session.execute(stmt).all()
        if len(rows) < 2:
            logger.info("signal.similarity", avg_sim=0.0, note="insufficient_scripts")
            return 0.0

        # Concatenate script fields into single documents
        documents: list[str] = []
        for row in rows:
            text = " ".join(filter(None, [row.hook, row.body, row.cta]))
            documents.append(text)

        # Lazy imports to avoid loading sklearn when not needed
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        vectorizer = TfidfVectorizer()
        tfidf_matrix = vectorizer.fit_transform(documents)
        sim_matrix = cosine_similarity(tfidf_matrix)

        # Average the upper-triangle (excluding diagonal) of the similarity matrix
        n = sim_matrix.shape[0]
        pair_count = 0
        pair_sum = 0.0
        for i in range(n):
            for j in range(i + 1, n):
                pair_sum += sim_matrix[i, j]
                pair_count += 1

        avg_sim = pair_sum / pair_count if pair_count > 0 else 0.0
        logger.info("signal.similarity", avg_sim=round(avg_sim, 4), scripts=len(documents))
        return avg_sim
    except Exception:
        logger.exception("signal.similarity.error")
        return 0.0


def _count_cadence(session: Session, days: int = 7) -> int:
    """Count publications created in the last *days* days."""
    try:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(func.count())
            .select_from(Publication)
            .where(
                Publication.posted_at.isnot(None),
                Publication.posted_at >= cutoff,
            )
        )
        result: int = session.execute(stmt).scalar() or 0
        logger.info("signal.cadence", count=result, days=days)
        return result
    except Exception:
        logger.exception("signal.cadence.error")
        return 0


# ---------------------------------------------------------------------------
# Risk-level classification
# ---------------------------------------------------------------------------


def _classify_risk(score: float) -> str:
    """Return 'LOW', 'MEDIUM', or 'HIGH' based on the final score."""
    for threshold, level in _RISK_LEVELS:
        if score <= threshold:
            return level
    return "HIGH"


# ---------------------------------------------------------------------------
# Main Celery task
# ---------------------------------------------------------------------------


@celery.task(name="services.account_health.evaluate_account_health", bind=True, max_retries=2)
def evaluate_account_health(self, run_date: str) -> dict[str, Any]:  # noqa: C901
    """Evaluate account health and gate the daily pipeline.

    Parameters
    ----------
    run_date:
        ISO-formatted date string (``YYYY-MM-DD``) for the current run.

    Returns
    -------
    dict
        Keys: ``run_id``, ``risk_score``, ``risk_level``, ``posts_allowed``,
        ``cooldown_minutes``, ``safe_mode``, ``components``.
    """
    logger.info("account_health.start", run_date=run_date)
    parsed_date = date.fromisoformat(run_date)
    now = datetime.now(UTC)

    try:
        with get_session_cm() as session:
            # ---- Collect raw signals ----
            raw_removals = _count_removals(session)
            raw_strikes = _count_strikes(session)
            raw_reach_drop = _calc_reach_drop(session)
            raw_similarity = _calc_content_similarity(session)
            raw_cadence = _count_cadence(session)

            # ---- Score each signal (0-100) ----
            score_removals = _score_removals(raw_removals)
            score_strikes = _score_strikes(raw_strikes)
            score_reach = _score_reach_drop(raw_reach_drop)
            score_sim = _score_similarity(raw_similarity)
            score_cad = _score_cadence(raw_cadence)

            # ---- Apply decay per component ----
            # For removals: days since most recent removal
            days_since_removal = _days_since_last_removal(session)
            score_removals = _apply_decay(score_removals, days_since_removal)

            # Strikes: no automated date tracking yet, so no decay applied
            # Reach drop: inherently recent, no additional decay
            # Similarity: inherently current, no additional decay
            # Cadence: inherently a rolling window, no additional decay

            # ---- Weighted risk score ----
            risk_score = (
                score_removals * settings.AH_WEIGHT_REMOVALS
                + score_strikes * settings.AH_WEIGHT_STRIKES
                + score_reach * settings.AH_WEIGHT_REACH_DROP
                + score_sim * settings.AH_WEIGHT_SIMILARITY
                + score_cad * settings.AH_WEIGHT_CADENCE
            )
            risk_score = round(min(max(risk_score, 0.0), 100.0), 2)

            risk_level = _classify_risk(risk_score)
            posts_allowed = _POSTS_ALLOWED[risk_level]
            cooldown_minutes = _COOLDOWN_MINUTES[risk_level]
            safe_mode = risk_level == "HIGH"

            components = {
                "removals": {
                    "raw": raw_removals,
                    "score": round(score_removals, 2),
                    "weight": settings.AH_WEIGHT_REMOVALS,
                    "days_since": days_since_removal,
                },
                "strikes": {
                    "raw": raw_strikes,
                    "score": round(score_strikes, 2),
                    "weight": settings.AH_WEIGHT_STRIKES,
                },
                "reach_drop": {
                    "raw": round(raw_reach_drop, 4),
                    "score": round(score_reach, 2),
                    "weight": settings.AH_WEIGHT_REACH_DROP,
                },
                "similarity": {
                    "raw": round(raw_similarity, 4),
                    "score": round(score_sim, 2),
                    "weight": settings.AH_WEIGHT_SIMILARITY,
                },
                "cadence": {
                    "raw": raw_cadence,
                    "score": round(score_cad, 2),
                    "weight": settings.AH_WEIGHT_CADENCE,
                },
            }

            # ---- Persist to database ----
            run = session.execute(
                select(DailyRun).where(DailyRun.run_date == parsed_date)
            ).scalar_one_or_none()

            if run is None:
                run = DailyRun(
                    run_date=parsed_date,
                    risk_score=risk_score,
                    risk_level=risk_level,
                    posts_allowed=posts_allowed,
                    cooldown_minutes=cooldown_minutes,
                    status="running",
                    started_at=now,
                    limits_json=components,
                )
                session.add(run)
            else:
                run.risk_score = risk_score
                run.risk_level = risk_level
                run.posts_allowed = posts_allowed
                run.cooldown_minutes = cooldown_minutes
                run.limits_json = components
                run.status = "running"
                run.started_at = now

            session.flush()
            run_id = run.id

        result = {
            "run_id": run_id,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "posts_allowed": posts_allowed,
            "cooldown_minutes": cooldown_minutes,
            "safe_mode": safe_mode,
            "components": components,
        }

        logger.info(
            "account_health.done",
            run_id=run_id,
            risk_score=risk_score,
            risk_level=risk_level,
            posts_allowed=posts_allowed,
        )
        return result

    except Exception as exc:
        logger.exception("account_health.failed", run_date=run_date)
        # Fail-safe: assume HIGH risk when evaluation itself fails
        return {
            "run_id": None,
            "risk_score": 100.0,
            "risk_level": "HIGH",
            "posts_allowed": 0,
            "cooldown_minutes": 0,
            "safe_mode": True,
            "components": {},
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _days_since_last_removal(session: Session) -> float:
    """Return the number of days since the most recent removed publication.

    If there are no removals on record, returns 999.0 so that decay drives the
    removal component score to zero.
    """
    try:
        stmt = select(func.max(Publication.posted_at)).where(
            Publication.status == "FAILED",
            Publication.posted_at.isnot(None),
        )
        last_removal_at: datetime | None = session.execute(stmt).scalar()
        if last_removal_at is None:
            return 999.0
        delta = datetime.now(UTC) - last_removal_at
        return max(delta.total_seconds() / 86400.0, 0.0)
    except Exception:
        logger.exception("days_since_last_removal.error")
        return 0.0  # fail-safe: no decay applied
