"""Runtime settings stored in Redis — toggleable from the dashboard."""

from __future__ import annotations

import redis

from app.config import settings
from app.logging import get_logger

logger = get_logger(service="runtime_settings")

_REDIS_KEY_AUTO_PUBLISH = "aiafiliado:auto_publish"


def _get_redis() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=5)


def get_auto_publish() -> bool:
    """Return True if auto-publish is enabled (default: True)."""
    try:
        r = _get_redis()
        val = r.get(_REDIS_KEY_AUTO_PUBLISH)
        if val is None:
            return False  # default: disabled (manual publishing)
        return val == "1"
    except Exception:
        logger.warning("runtime_settings.redis_error", key=_REDIS_KEY_AUTO_PUBLISH, exc_info=True)
        return False  # fail-safe: don't publish if Redis is unreachable


def set_auto_publish(enabled: bool) -> None:
    """Set auto-publish on/off."""
    try:
        r = _get_redis()
        r.set(_REDIS_KEY_AUTO_PUBLISH, "1" if enabled else "0")
        logger.info("runtime_settings.auto_publish_changed", enabled=enabled)
    except Exception:
        logger.exception("runtime_settings.set_error", key=_REDIS_KEY_AUTO_PUBLISH)
        raise
