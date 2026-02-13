"""Reusable filter components for the dashboard sidebar/pages."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from dashboard.config import dash_settings


def date_range_filter(key: str = "date_range") -> int:
    """Date range selector returning number of days.

    Returns the selected period in days.
    """
    options = {
        "7 dias": 7,
        "14 dias": 14,
        "30 dias": 30,
        "60 dias": 60,
        "90 dias": 90,
    }
    selected = st.selectbox(
        "Periodo",
        options=list(options.keys()),
        index=0,
        key=key,
    )
    return options[selected]


def platform_filter(
    platforms: list[str] | None = None,
    key: str = "platform",
    include_all: bool = True,
) -> str | None:
    """Platform selector dropdown.

    Returns selected platform string or None for 'All'.
    """
    if platforms is None:
        platforms = ["tiktok_shop", "hotmart", "monetizze", "amazon", "shopee"]
    opts = ["Todas"] + platforms if include_all else platforms
    selected = st.selectbox("Plataforma", options=opts, key=key)
    return None if selected == "Todas" else selected


def status_filter(
    statuses: list[str],
    key: str = "status",
    include_all: bool = True,
) -> str | None:
    """Status selector dropdown.

    Returns selected status string or None for 'All'.
    """
    opts = ["Todos"] + statuses if include_all else statuses
    selected = st.selectbox("Status", options=opts, key=key)
    return None if selected == "Todos" else selected
