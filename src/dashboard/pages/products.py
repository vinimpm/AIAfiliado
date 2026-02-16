"""Products page — Product table, winners, platform distribution, top revenue."""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st
from sqlalchemy.orm import Session

from sqlalchemy import select

from dashboard.components.charts import bar_chart, pie_chart
from dashboard.components.filters import platform_filter, status_filter
from dashboard.data import queries
from models.product import Product

_PLATFORMS = ["amazon", "shopee", "hotmart", "monetizze", "tiktok_shop"]
_CATEGORIES = [
    "beauty",
    "skincare",
    "fashion",
    "accessories",
    "home",
    "kitchen",
    "tech_accessories",
    "fitness",
    "health",
    "education",
    "pet",
]


def render(session: Session):
    st.header("Produtos")

    # --- Add Product Form ---
    with st.expander("Adicionar Produto Manualmente", expanded=False):
        with st.form("add_product_form", clear_on_submit=True):
            st.caption("Preencha os dados do produto para adicionar ao pipeline.")

            col_a, col_b = st.columns(2)
            with col_a:
                title = st.text_input("Nome do Produto *")
                price = st.number_input("Preco (R$) *", min_value=0.01, value=49.90, step=0.01)
                commission_pct = st.number_input(
                    "Comissao (%) *", min_value=0.1, max_value=100.0, value=10.0, step=0.5
                )

            with col_b:
                source_platform = st.selectbox("Plataforma *", options=_PLATFORMS)
                category = st.selectbox("Categoria *", options=_CATEGORIES)
                affiliate_url = st.text_input("Link de Afiliado *")

            commission_value = round(price * commission_pct / 100, 2)
            st.caption(f"Comissao estimada: **R$ {commission_value:.2f}** por venda")

            submitted = st.form_submit_button("Adicionar Produto", use_container_width=True)

            if submitted:
                if not title or not affiliate_url:
                    st.error("Preencha todos os campos obrigatorios.")
                else:
                    product = Product(
                        source="manual",
                        source_platform=source_platform,
                        title=title.strip(),
                        price=price,
                        commission=commission_value,
                        affiliate_url=affiliate_url.strip(),
                        category=category,
                        status="validated",
                        is_active=True,
                        validated_at=datetime.now(UTC),
                    )
                    session.add(product)
                    session.commit()
                    st.success(
                        f"Produto '{title}' adicionado! "
                        f"Comissao: R$ {commission_value:.2f} ({commission_pct}%)"
                    )

    st.divider()

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
        st.dataframe(df_prods, width="stretch", hide_index=True)
        st.caption(f"{len(df_prods)} produtos encontrados")

        # --- Reactivate retired products ---
        retired = session.execute(
            select(Product).where(Product.status == "retired")
        ).scalars().all()
        if retired:
            with st.expander(f"Reativar Produtos Retirados ({len(retired)})", expanded=False):
                st.caption(
                    "Produtos abaixo foram retirados automaticamente. "
                    "Clique para reativar e incluir no proximo pipeline."
                )
                for p in retired:
                    col_name, col_btn = st.columns([4, 1])
                    col_name.write(f"**{p.title}** ({p.source_platform})")
                    if col_btn.button("Reativar", key=f"reactivate_{p.id}"):
                        p.status = "validated"
                        p.is_active = True
                        p.validated_at = datetime.now(UTC)
                        session.commit()
                        st.success(f"Produto '{p.title}' reativado!")
                        st.rerun()
    else:
        st.info("Nenhum produto encontrado com os filtros selecionados.")

    st.divider()

    # --- Winners ---
    st.subheader("Vencedores (Ativos)")
    df_winners = queries.winners(session)
    if not df_winners.empty:
        st.dataframe(df_winners, width="stretch", hide_index=True)
        total_rev = df_winners["total_revenue"].sum()
        total_sales = df_winners["total_sales"].sum()
        st.markdown(f"**Total:** {int(total_sales)} vendas | R$ {float(total_rev):,.2f} receita")
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
                df_plat,
                names="source_platform",
                values="count",
            )
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("Sem dados.")

    # --- Top by Revenue ---
    with col2:
        st.subheader("Top 10 por Receita")
        df_top = queries.top_products_by_revenue(session, limit=10)
        if not df_top.empty:
            fig = bar_chart(
                df_top,
                x="total_revenue",
                y="title",
                labels={"total_revenue": "Receita (R$)", "title": "Produto"},
            )
            fig.update_layout(yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("Sem dados.")
