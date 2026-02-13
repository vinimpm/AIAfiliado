"""Pipeline page — Daily runs table, risk trend, success rate, duration."""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.charts import bar_chart, line_chart
from dashboard.components.filters import date_range_filter
from dashboard.data import queries


def _style_risk_level(val: str) -> str:
    colors = {"LOW": "#2ecc71", "MEDIUM": "#f39c12", "HIGH": "#e74c3c"}
    color = colors.get(val, "#888")
    return f"color: {color}; font-weight: bold"


def render(session: Session):
    st.header("Pipeline")

    days = date_range_filter(key="pipeline_period")

    # --- Daily Runs Table ---
    st.subheader("Execucoes Diarias")
    df_runs = queries.daily_runs_table(session, days=days)
    if not df_runs.empty:
        styled = df_runs.style.map(_style_risk_level, subset=["risk_level"])
        st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        st.info("Sem execucoes no periodo.")

    st.divider()

    # --- Charts ---
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Risk Score ao Longo do Tempo")
        df_risk = queries.risk_score_trend(session, days=days)
        if not df_risk.empty:
            fig = line_chart(
                df_risk,
                x="run_date",
                y="risk_score",
                labels={"run_date": "Data", "risk_score": "Risk Score"},
            )
            fig.add_hline(y=70, line_dash="dash", line_color="red", annotation_text="HIGH")
            fig.add_hline(y=40, line_dash="dash", line_color="orange", annotation_text="MEDIUM")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sem dados.")

    with col2:
        st.subheader("Taxa de Sucesso")
        df_rate = queries.success_rate_trend(session, days=days)
        if not df_rate.empty:
            fig = line_chart(
                df_rate,
                x="run_date",
                y="success_rate",
                labels={"run_date": "Data", "success_rate": "Taxa (%)"},
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sem dados.")

    st.subheader("Duracao das Execucoes")
    df_dur = queries.run_duration_chart(session, days=days)
    if not df_dur.empty:
        fig = bar_chart(
            df_dur,
            x="run_date",
            y="duration_min",
            labels={"run_date": "Data", "duration_min": "Minutos"},
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Sem dados de duracao.")
