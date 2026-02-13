"""Performance page — Engagement, retention, funnel, A/B, top publications."""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.charts import line_chart, histogram, funnel_chart
from dashboard.components.filters import date_range_filter
from dashboard.data import queries


def render(session: Session):
    st.header("Performance")

    days = date_range_filter(key="perf_period")

    # --- Engagement Over Time ---
    st.subheader("Engajamento por Dia")
    df_eng = queries.engagement_over_time(session, days=days)
    if not df_eng.empty:
        fig = line_chart(
            df_eng, x="day", y=["views", "likes", "shares"],
            labels={"day": "Data", "value": "Total", "variable": "Metrica"},
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Sem dados de engajamento.")

    col1, col2 = st.columns(2)

    # --- Retention Histogram ---
    with col1:
        st.subheader("Distribuicao de Retencao 3s")
        df_ret = queries.retention_distribution(session, days=days)
        if not df_ret.empty:
            fig = histogram(
                df_ret, x="retention_3s",
                labels={"retention_3s": "Retencao 3s"},
                nbins=20,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sem dados de retencao.")

    # --- Funnel ---
    with col2:
        st.subheader("Funil de Conversao")
        funnel = queries.funnel_data(session, days=days)
        if funnel["views"] > 0:
            fig = funnel_chart(
                stages=["Views", "Clicks", "Sales"],
                values=[funnel["views"], funnel["clicks"], funnel["sales"]],
            )
            st.plotly_chart(fig, use_container_width=True)
            st.markdown(
                f"**CTR:** {funnel['ctr']:.2%} | **CVR:** {funnel['cvr']:.2%}"
            )
        else:
            st.info("Sem dados de conversao.")

    st.divider()

    # --- A/B Comparison ---
    st.subheader("Comparacao A/B")
    df_ab = queries.ab_comparison(session, days=days)
    if not df_ab.empty:
        st.dataframe(df_ab, use_container_width=True, hide_index=True)
    else:
        st.info("Sem dados A/B no periodo.")

    # --- Top 10 Publications ---
    st.subheader("Top 10 Publicacoes por Views")
    df_top = queries.top_publications(session, days=days, limit=10)
    if not df_top.empty:
        st.dataframe(df_top, use_container_width=True, hide_index=True)
    else:
        st.info("Sem publicacoes no periodo.")
