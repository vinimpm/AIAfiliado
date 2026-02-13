"""Plotly chart builders for the dashboard."""

from __future__ import annotations

import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

from dashboard.config import dash_settings

_TEMPLATE = dash_settings.PLOTLY_TEMPLATE


def line_chart(
    df: pd.DataFrame,
    x: str,
    y: str | list[str],
    title: str = "",
    labels: dict | None = None,
) -> go.Figure:
    """Multi-line chart."""
    if isinstance(y, list):
        fig = px.line(df, x=x, y=y, title=title, labels=labels, template=_TEMPLATE)
    else:
        fig = px.line(df, x=x, y=y, title=title, labels=labels, template=_TEMPLATE)
    fig.update_layout(hovermode="x unified")
    return fig


def bar_chart(
    df: pd.DataFrame,
    x: str,
    y: str,
    title: str = "",
    color: str | None = None,
    labels: dict | None = None,
    barmode: str = "relative",
) -> go.Figure:
    """Bar chart."""
    fig = px.bar(
        df, x=x, y=y, title=title, color=color,
        labels=labels, template=_TEMPLATE, barmode=barmode,
    )
    return fig


def stacked_bar_chart(
    df: pd.DataFrame,
    x: str,
    y: str,
    color: str,
    title: str = "",
    labels: dict | None = None,
) -> go.Figure:
    """Stacked bar chart."""
    fig = px.bar(
        df, x=x, y=y, color=color, title=title,
        labels=labels, template=_TEMPLATE, barmode="stack",
    )
    return fig


def pie_chart(
    df: pd.DataFrame,
    names: str,
    values: str,
    title: str = "",
) -> go.Figure:
    """Pie chart."""
    fig = px.pie(df, names=names, values=values, title=title, template=_TEMPLATE)
    return fig


def histogram(
    df: pd.DataFrame,
    x: str,
    title: str = "",
    nbins: int = 20,
    labels: dict | None = None,
) -> go.Figure:
    """Histogram."""
    fig = px.histogram(
        df, x=x, title=title, nbins=nbins,
        labels=labels, template=_TEMPLATE,
    )
    return fig


def gauge_chart(
    value: float,
    title: str = "",
    max_val: float = 100,
    thresholds: dict | None = None,
) -> go.Figure:
    """Gauge chart with color thresholds.

    Args:
        value: Current value.
        title: Chart title.
        max_val: Maximum gauge value.
        thresholds: Dict with 'green', 'yellow', 'red' upper bounds.
            Defaults to 0-40 green, 40-70 yellow, 70-100 red.
    """
    if thresholds is None:
        thresholds = {"green": 40, "yellow": 70, "red": 100}

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        title={"text": title},
        gauge={
            "axis": {"range": [0, max_val]},
            "bar": {"color": "white"},
            "steps": [
                {"range": [0, thresholds["green"]], "color": "#2ecc71"},
                {"range": [thresholds["green"], thresholds["yellow"]], "color": "#f39c12"},
                {"range": [thresholds["yellow"], thresholds["red"]], "color": "#e74c3c"},
            ],
        },
    ))
    fig.update_layout(template=_TEMPLATE, height=300)
    return fig


def funnel_chart(
    stages: list[str],
    values: list[int],
    title: str = "",
) -> go.Figure:
    """Funnel chart for conversion stages."""
    fig = go.Figure(go.Funnel(
        y=stages,
        x=values,
        textinfo="value+percent initial",
    ))
    fig.update_layout(title=title, template=_TEMPLATE)
    return fig
