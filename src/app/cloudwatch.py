"""CloudWatch custom metrics emitter.

Emits application-level metrics to the ``AIAfiliado`` CloudWatch namespace
in production. No-ops silently in development/test environments.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_NAMESPACE = "AIAfiliado"
_client: Any = None


def _get_client():
    """Lazy-init a boto3 CloudWatch client."""
    global _client
    if _client is None:
        import boto3

        _client = boto3.client("cloudwatch", region_name=settings.AWS_REGION)
    return _client


def emit_metric(name: str, value: float, unit: str = "Count") -> None:
    """Emit a custom metric to CloudWatch.

    In non-production environments this is a no-op.

    Parameters
    ----------
    name:
        Metric name (e.g. ``VideosGenerated``, ``DailyCostUSD``).
    value:
        Numeric value.
    unit:
        CloudWatch unit — ``Count``, ``None``, ``Seconds``, etc.
    """
    if settings.ENV != "production":
        logger.debug("cloudwatch.emit_metric.skip (env=%s)", settings.ENV)
        return

    try:
        _get_client().put_metric_data(
            Namespace=_NAMESPACE,
            MetricData=[
                {
                    "MetricName": name,
                    "Value": value,
                    "Unit": unit,
                }
            ],
        )
    except Exception:
        logger.exception("cloudwatch.emit_metric.error", extra={"metric": name})
