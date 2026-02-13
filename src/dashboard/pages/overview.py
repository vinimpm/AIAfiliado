"""Overview page — KPIs, today's run status, and alerts."""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.charts import stacked_bar_chart
from dashboard.components.filters import date_range_filter
from dashboard.components.kpi_card import kpi_row
from dashboard.data import queries


def _risk_color(level: str) -> str:
    return {"LOW": "green", "MEDIUM": "orange", "HIGH": "red"}.get(level, "gray")


def render(session: Session):
    st.header("Overview")

    days = date_range_filter(key="overview_period")

    # --- KPIs ---
    kpis = queries.kpi_totals(session, days=days)
    kpi_row([
        {"label": "Videos Gerados", "value": kpis["videos"]},
        {"label": "Publicacoes", "value": kpis["publications"]},
        {"label": "Views", "value": kpis["views"]},
        {"label": "Vendas", "value": kpis["sales"]},
        {"label": "Receita", "value": kpis["revenue"], "prefix": "R$ "},
        {"label": "Retencao 3s", "value": kpis["avg_retention"] * 100, "suffix": "%"},
    ], columns=6)

    st.divider()

    # --- Today's run ---
    col_run, col_chart = st.columns([1, 2])

    with col_run:
        st.subheader("Pipeline Hoje")
        run = queries.today_run(session)
        if run:
            color = _risk_color(run["risk_level"])
            st.markdown(
                f"**Risk Level:** :{color}[{run['risk_level']}] "
                f"(score: {run['risk_score']:.0f})"
            )
            st.markdown(f"**Posts Allowed:** {run['posts_allowed']}")
            st.markdown(f"**Cooldown:** {run['cooldown_minutes']} min")
            status_colors = {
                "running": "blue", "completed": "green",
                "failed": "red", "paused": "orange",
            }
            sc = status_colors.get(run["status"], "gray")
            st.markdown(f"**Status:** :{sc}[{run['status'].upper()}]")
        else:
            st.info("Nenhuma execucao hoje.")

    with col_chart:
        st.subheader(f"Status dos Ultimos {days} Dias")
        df_status = queries.run_status_by_day(session, days=days)
        if not df_status.empty:
            # Count by status per day
            counts = df_status.groupby(["run_date", "status"]).size().reset_index(name="count")
            fig = stacked_bar_chart(
                counts, x="run_date", y="count", color="status",
                title="", labels={"run_date": "Data", "count": "Runs"},
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sem dados de execucao no periodo.")

    # --- Alerts ---
    st.subheader("Alertas")
    alert_items = queries.alerts(session)
    if alert_items:
        for alert in alert_items:
            if alert["level"] == "error":
                st.error(alert["message"])
            elif alert["level"] == "warning":
                st.warning(alert["message"])
            else:
                st.info(alert["message"])
    else:
        st.success("Nenhum alerta ativo.")
