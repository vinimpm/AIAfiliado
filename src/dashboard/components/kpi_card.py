"""Reusable KPI card component wrapping st.metric."""

from __future__ import annotations

import streamlit as st


def kpi_card(
    label: str,
    value: str | int | float,
    delta: str | None = None,
    delta_color: str = "normal",
    prefix: str = "",
    suffix: str = "",
):
    """Render a single KPI metric card.

    Args:
        label: Metric label.
        value: Main value to display.
        delta: Optional delta string (e.g. "+12%").
        delta_color: "normal", "inverse", or "off".
        prefix: Text before value (e.g. "R$").
        suffix: Text after value (e.g. "%").
    """
    display = f"{prefix}{value:,.2f}{suffix}" if isinstance(value, float) else f"{prefix}{value:,}{suffix}"
    st.metric(label=label, value=display, delta=delta, delta_color=delta_color)


def kpi_row(metrics: list[dict], columns: int | None = None):
    """Render multiple KPI cards in a row of columns.

    Args:
        metrics: List of dicts with keys matching kpi_card params.
        columns: Number of columns (defaults to len(metrics)).
    """
    cols = st.columns(columns or len(metrics))
    for col, m in zip(cols, metrics):
        with col:
            kpi_card(**m)
