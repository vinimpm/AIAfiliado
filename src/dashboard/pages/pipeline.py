"""Pipeline page — Daily runs table, risk trend, success rate, duration."""

from __future__ import annotations

import json
import uuid

import redis
import streamlit as st
from sqlalchemy.orm import Session

from app.config import settings
from dashboard.components.charts import bar_chart, line_chart
from dashboard.components.filters import date_range_filter
from dashboard.data import queries


def _send_pipeline_task() -> str:
    """Send the pipeline task directly via Redis (avoids result backend issues)."""
    task_id = str(uuid.uuid4())
    body = json.dumps({
        "id": task_id,
        "task": "services.orchestrator.trigger_daily_pipeline",
        "args": [],
        "kwargs": {},
        "retries": 0,
    })
    headers = {
        "lang": "py",
        "task": "services.orchestrator.trigger_daily_pipeline",
        "id": task_id,
        "root_id": task_id,
        "argsrepr": "()",
        "kwargsrepr": "{}",
    }
    message = json.dumps({
        "body": body,
        "content-encoding": "utf-8",
        "content-type": "application/json",
        "headers": headers,
        "properties": {
            "correlation_id": task_id,
            "delivery_mode": 2,
            "delivery_tag": str(uuid.uuid4()),
            "body_encoding": "utf-8",
            "delivery_info": {"exchange": "", "routing_key": "celery"},
        },
    })
    r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=5)
    r.lpush("celery", message)
    return task_id


def _style_risk_level(val: str) -> str:
    colors = {"LOW": "#2ecc71", "MEDIUM": "#f39c12", "HIGH": "#e74c3c"}
    color = colors.get(val, "#888")
    return f"color: {color}; font-weight: bold"


def render(session: Session):
    st.header("Pipeline")

    # --- Trigger Pipeline ---
    col_trigger, col_status = st.columns([1, 2])
    with col_trigger:
        if st.button("Disparar Pipeline Agora", type="primary", use_container_width=True):
            try:
                task_id = _send_pipeline_task()
                st.success(f"Pipeline disparado! Acompanhe o status abaixo.")
            except Exception as e:
                st.error(f"Erro ao disparar: {e}")

    with col_status:
        run = queries.today_run(session)
        if run:
            status_colors = {
                "running": ":blue[EXECUTANDO...]",
                "completed": ":green[CONCLUIDO]",
                "failed": ":red[FALHOU]",
                "paused": ":orange[PAUSADO]",
            }
            sc = status_colors.get(run["status"], run["status"])
            st.markdown(f"**Pipeline Hoje:** {sc}")
            st.caption(
                f"Risk: {run['risk_level']} | "
                f"Posts: {run['posts_allowed']} | "
                f"Cooldown: {run['cooldown_minutes']}min"
            )
        else:
            st.info("Nenhuma execucao hoje. Clique no botao para disparar.")

    st.divider()

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
