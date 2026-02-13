"""Products page — Product table, winners, platform distribution, top revenue."""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.charts import pie_chart, bar_chart
from dashboard.components.filters import platform_filter, status_filter
from dashboard.data import queries


def render(session: Session):
    st.header("Produtos")

    # --- Filters ---
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        platform = platform_filter(key="prod_platform")
    with col_f2:
        status = status_filter(
            statuses=["pending", "validated", "rejected", "retired"],
            key="prod_status",
        )

    # --- Products Table ---
    st.subheader("Catalogo de Produtos")
    df_prods = queries.products_table(session, platform=platform, status=status)
    if not df_prods.empty:
        st.dataframe(df_prods, use_container_width=True, hide_index=True)
        st.caption(f"{len(df_prods)} produtos encontrados")
    else:
        st.info("Nenhum produto encontrado com os filtros selecionados.")

    st.divider()

    # --- Winners ---
    st.subheader("Vencedores (Ativos)")
    df_winners = queries.winners(session)
    if not df_winners.empty:
        st.dataframe(df_winners, use_container_width=True, hide_index=True)
        total_rev = df_winners["total_revenue"].sum()
        total_sales = df_winners["total_sales"].sum()
        st.markdown(
            f"**Total:** {int(total_sales)} vendas | "
            f"R$ {float(total_rev):,.2f} receita"
        )
    else:
        st.info("Nenhum produto vencedor ativo.")

    st.divider()

    col1, col2 = st.columns(2)

    # --- Platform Distribution ---
    with col1:
        st.subheader("Distribuicao por Plataforma")
        df_plat = queries.products_by_platform(session)
        if not df_plat.empty:
            fig = pie_chart(
                df_plat, names="source_platform", values="count",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sem dados.")

    # --- Top by Revenue ---
    with col2:
        st.subheader("Top 10 por Receita")
        df_top = queries.top_products_by_revenue(session, limit=10)
        if not df_top.empty:
            fig = bar_chart(
                df_top, x="total_revenue", y="title",
                labels={"total_revenue": "Receita (R$)", "title": "Produto"},
            )
            fig.update_layout(yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sem dados.")
