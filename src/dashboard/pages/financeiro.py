"""Financeiro page — Cost vs revenue, ROI, budget gauge, cumulative P&L."""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from app.config import settings
from dashboard.components.charts import gauge_chart, line_chart
from dashboard.components.filters import date_range_filter
from dashboard.components.kpi_card import kpi_row
from dashboard.data import queries


def render(session: Session):
    st.header("Financeiro")

    days = date_range_filter(key="fin_period")

    # --- KPIs ---
    fin = queries.financial_kpis(session, days=days)
    kpi_row([
        {"label": "Custo Total (USD)", "value": fin["cost"], "prefix": "$ "},
        {"label": "Receita Total (R$)", "value": fin["revenue"], "prefix": "R$ "},
        {"label": "Lucro (R$)", "value": fin["profit"], "prefix": "R$ "},
        {"label": "ROI", "value": fin["roi"], "suffix": "%"},
    ], columns=4)

    st.divider()

    col1, col2 = st.columns(2)

    # --- Budget Gauge ---
    with col1:
        st.subheader("Orcamento Diario")
        budget = queries.budget_usage_today(session, daily_budget=settings.DAILY_BUDGET_USD)
        fig = gauge_chart(
            value=budget["pct"],
            title=f"Utilizado: ${budget['used']:.2f} / ${budget['budget']:.2f}",
            max_val=100,
            thresholds={"green": 60, "yellow": 85, "red": 100},
        )
        st.plotly_chart(fig, use_container_width=True)

    # --- Cost vs Revenue ---
    with col2:
        st.subheader("Custo vs Receita")
        df_cr = queries.daily_cost_revenue(session, days=days)
        if not df_cr.empty:
            fig = line_chart(
                df_cr, x="day", y=["cost", "revenue"],
                labels={"day": "Data", "value": "Valor", "variable": "Tipo"},
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sem dados financeiros no periodo.")

    # --- Cumulative P&L ---
    st.subheader("P&L Acumulado")
    df_pnl = queries.cumulative_pnl(session, days=days)
    if not df_pnl.empty:
        fig = line_chart(
            df_pnl, x="day", y="pnl",
            labels={"day": "Data", "pnl": "P&L Acumulado (R$)"},
        )
        fig.add_hline(y=0, line_dash="dash", line_color="gray")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Sem dados de P&L.")
