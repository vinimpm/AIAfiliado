"""AIAfiliado Dashboard — Streamlit entry point."""

from __future__ import annotations

import streamlit as st

from dashboard.config import dash_settings
from dashboard.pages import financeiro, health, overview, performance, pipeline, products
from models.database import get_session_cm

st.set_page_config(
    page_title=dash_settings.PAGE_TITLE,
    page_icon=dash_settings.PAGE_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Sidebar Navigation ---
st.sidebar.title("AIAfiliado")
st.sidebar.caption("Dashboard de Monitoramento")

PAGES = {
    "Overview": overview,
    "Pipeline": pipeline,
    "Performance": performance,
    "Produtos": products,
    "Financeiro": financeiro,
    "Account Health": health,
}

selected = st.sidebar.radio("Navegacao", list(PAGES.keys()))

st.sidebar.divider()
st.sidebar.caption(f"Auto-refresh: {dash_settings.REFRESH_INTERVAL_SECONDS}s")
st.sidebar.caption(f"Cache TTL: {dash_settings.CACHE_TTL_SECONDS}s")

# --- Auto-refresh ---
st_autorefresh = None
try:
    from streamlit_autorefresh import st_autorefresh  # type: ignore[no-redef]
    st_autorefresh(
        interval=dash_settings.REFRESH_INTERVAL_SECONDS * 1000,
        key="auto_refresh",
    )
except ImportError:
    pass

# --- Render Selected Page ---
with get_session_cm() as session:
    PAGES[selected].render(session)
