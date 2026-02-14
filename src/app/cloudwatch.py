"""Metrics emitter via structured logging.

Emits application-level metrics as structured log entries. Railway captures
stdout automatically, making these metrics searchable in the Observability tab.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def emit_metric(name: str, value: float, unit: str = "Count") -> None:
    """Emit a metric as a structured log entry.

    Parameters
    ----------
    name:
        Metric name (e.g. ``VideosGenerated``, ``DailyCostUSD``).
    value:
        Numeric value.
    unit:
        Unit label — ``Count``, ``None``, ``Seconds``, etc.
    """
    logger.info(
        "metric.emitted",
        extra={"metric_name": name, "metric_value": value, "metric_unit": unit},
    )
