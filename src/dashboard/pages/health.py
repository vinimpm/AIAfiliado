"""Account Health page — Risk gauge, history, posts usage, cooldown."""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.charts import bar_chart, gauge_chart, line_chart
from dashboard.components.filters import date_range_filter
from dashboard.data import queries


def _risk_color(level: str) -> str:
    return {"LOW": "green", "MEDIUM": "orange", "HIGH": "red"}.get(level, "gray")


def _level_description(level: str) -> str:
    return {
        "LOW": "Conta saudavel. Pipeline operando normalmente.",
        "MEDIUM": "Atencao recomendada. Limite de posts reduzido.",
        "HIGH": "Risco elevado! Pipeline pausado automaticamente.",
    }.get(level, "")


def render(session: Session):
    st.header("Account Health")

    days = date_range_filter(key="health_period")

    # --- Current Health ---
    health = queries.current_health(session)

    if health:
        col_gauge, col_info = st.columns([1, 1])

        with col_gauge:
            fig = gauge_chart(
                value=health["risk_score"],
                title="Risk Score Atual",
                max_val=100,
            )
            st.plotly_chart(fig, width="stretch")

        with col_info:
            color = _risk_color(health["risk_level"])
            st.markdown(f"### Nivel: :{color}[{health['risk_level']}]")
            st.markdown(_level_description(health["risk_level"]))
            st.markdown(f"**Posts Permitidos:** {health['posts_allowed']}")
            st.markdown(f"**Cooldown:** {health['cooldown_minutes']} min")
            st.markdown(f"**Data:** {health['run_date']}")

            if health["risk_level"] == "HIGH":
                st.error("Pipeline bloqueado por risco alto!")
            elif health["risk_level"] == "MEDIUM":
                st.warning("Limite de posts reduzido por precaucao.")
    else:
        st.info("Nenhum dado de saude disponivel.")

    st.divider()

    # --- Risk Score History ---
    st.subheader("Historico de Risk Score")
    df_hist = queries.health_history(session, days=days)
    if not df_hist.empty:
        fig = line_chart(
            df_hist,
            x="run_date",
            y="risk_score",
            labels={"run_date": "Data", "risk_score": "Risk Score"},
        )
        fig.add_hline(y=70, line_dash="dash", line_color="red", annotation_text="HIGH")
        fig.add_hline(y=40, line_dash="dash", line_color="orange", annotation_text="MEDIUM")
        st.plotly_chart(fig, width="stretch")

        # Timeline table with colored risk levels
        st.subheader("Timeline de Niveis")
        st.dataframe(
            df_hist.style.map(
                lambda v: f"color: {_risk_color(v)}; font-weight: bold",
                subset=["risk_level"],
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("Sem historico no periodo.")

    st.divider()

    col1, col2 = st.columns(2)

    # --- Posts Allowed vs Used ---
    with col1:
        st.subheader("Posts: Permitidos vs Usados")
        df_posts = queries.posts_allowed_vs_used(session, days=days)
        if not df_posts.empty:
            df_melted = df_posts.melt(
                id_vars="run_date",
                value_vars=["allowed", "used"],
                var_name="tipo",
                value_name="count",
            )
            fig = bar_chart(
                df_melted,
                x="run_date",
                y="count",
                color="tipo",
                labels={"run_date": "Data", "count": "Posts", "tipo": "Tipo"},
                barmode="group",
            )
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("Sem dados.")

    # --- Cooldown Trend ---
    with col2:
        st.subheader("Cooldown ao Longo do Tempo")
        df_cool = queries.cooldown_trend(session, days=days)
        if not df_cool.empty:
            fig = line_chart(
                df_cool,
                x="run_date",
                y="cooldown_minutes",
                labels={"run_date": "Data", "cooldown_minutes": "Minutos"},
            )
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("Sem dados de cooldown.")
