"""AIAfiliado Dashboard — Streamlit entry point."""

from __future__ import annotations

import hashlib

import streamlit as st

from app.runtime_settings import get_auto_publish, set_auto_publish
from dashboard.config import dash_settings
from dashboard.pages import financeiro, health, overview, performance, pipeline, products, videos
from models.database import get_session_cm


def _make_token() -> str:
    """Create a deterministic auth token from credentials."""
    raw = f"{dash_settings.AUTH_USER}:{dash_settings.AUTH_PASSWORD}:aiafiliado-secret"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _check_auth() -> bool:
    """Return True if the user is authenticated."""
    if not dash_settings.AUTH_PASSWORD:
        return True

    # Check session_state first (current session)
    if st.session_state.get("authenticated"):
        return True

    # Check query param token (survives refresh)
    token = st.query_params.get("token")
    if token and token == _make_token():
        st.session_state["authenticated"] = True
        return True

    return False


_authenticated = _check_auth()

st.set_page_config(
    page_title=dash_settings.PAGE_TITLE,
    page_icon=dash_settings.PAGE_ICON,
    layout="wide" if _authenticated else "centered",
    initial_sidebar_state="expanded" if _authenticated else "collapsed",
)

if not _authenticated:
    st.markdown(
        "<style>[data-testid='stSidebar']{display:none}</style>",
        unsafe_allow_html=True,
    )
    st.title("Login")
    with st.form("login_form"):
        username = st.text_input("Usuario")
        password = st.text_input("Senha", type="password")
        submitted = st.form_submit_button("Entrar")
        if submitted:
            if username == dash_settings.AUTH_USER and password == dash_settings.AUTH_PASSWORD:
                st.session_state["authenticated"] = True
                st.query_params["token"] = _make_token()
                st.rerun()
            else:
                st.error("Usuario ou senha incorretos.")
    st.stop()

# --- Sidebar Navigation ---
st.sidebar.title("AIAfiliado")
st.sidebar.caption("Dashboard de Monitoramento")

PAGES = {
    "Overview": overview,
    "Pipeline": pipeline,
    "Videos": videos,
    "Performance": performance,
    "Produtos": products,
    "Financeiro": financeiro,
    "Account Health": health,
}

selected = st.sidebar.radio("Navegacao", list(PAGES.keys()))

st.sidebar.divider()

# --- Auto-Publish Toggle ---
_current_auto_pub = get_auto_publish()
auto_pub_toggle = st.sidebar.toggle(
    "Auto-Publish",
    value=_current_auto_pub,
    help="Quando ativado, videos sao publicados automaticamente no TikTok. "
    "Quando desativado, videos sao gerados mas NAO publicados.",
)
if auto_pub_toggle != _current_auto_pub:
    set_auto_publish(auto_pub_toggle)
    st.sidebar.success(
        f"Auto-Publish {'ativado' if auto_pub_toggle else 'desativado'}!"
    )

st.sidebar.divider()
st.sidebar.caption(f"Auto-refresh: {dash_settings.REFRESH_INTERVAL_SECONDS}s")
st.sidebar.caption(f"Cache TTL: {dash_settings.CACHE_TTL_SECONDS}s")

if st.sidebar.button("Sair"):
    st.session_state["authenticated"] = False
    st.query_params.clear()
    st.rerun()

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
