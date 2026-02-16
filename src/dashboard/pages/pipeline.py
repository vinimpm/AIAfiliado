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

    # --- Today's Run Status (prominent) ---
    run = queries.today_run(session)

    if run and run["status"] == "running":
        # Auto-refresh every 5s while pipeline is running
        try:
            from streamlit_autorefresh import st_autorefresh

            st_autorefresh(interval=5000, key="pipeline_refresh")
        except ImportError:
            st.caption("Atualize a pagina para ver o progresso.")

    # --- Status Card ---
    if run:
        status_map = {
            "running": ("EXECUTANDO...", "info"),
            "completed": ("CONCLUIDO", "success"),
            "failed": ("FALHOU", "error"),
            "paused": ("PAUSADO", "warning"),
        }
        label, msg_type = status_map.get(run["status"], (run["status"], "info"))

        col_s1, col_s2, col_s3, col_s4 = st.columns(4)
        col_s1.metric("Status", label)
        col_s2.metric("Risk Level", run["risk_level"])
        col_s3.metric("Posts Permitidos", run["posts_allowed"])
        col_s4.metric("Cooldown", f"{run['cooldown_minutes']}min")

        if run["status"] == "running":
            st.info("Pipeline em execucao... A pagina atualiza automaticamente a cada 5 segundos.")
        elif run["status"] == "completed":
            st.success("Pipeline concluido! Confira os videos na pagina Videos.")
        elif run["status"] == "failed":
            st.error("Pipeline falhou. Verifique os logs no Railway.")
        elif run["status"] == "paused":
            st.warning("Pipeline pausado devido a risco alto ou zero posts permitidos.")
    else:
        st.info("Nenhuma execucao hoje.")

    # --- Trigger Button ---
    can_trigger = not run or run["status"] in ("completed", "failed", "paused")
    if can_trigger:
        if st.button("Disparar Pipeline Agora", type="primary"):
            try:
                _send_pipeline_task()
                st.success("Pipeline disparado! Aguarde alguns segundos e atualize a pagina.")
                st.rerun()
            except Exception as e:
                st.error(f"Erro ao disparar: {e}")
    elif run and run["status"] == "running":
        st.button("Pipeline em execucao...", disabled=True)

    st.divider()

    days = date_range_filter(key="pipeline_period")

    # --- Daily Runs Table ---
    st.subheader("Execucoes Diarias")
    df_runs = queries.daily_runs_table(session, days=days)
    if not df_runs.empty:
        styled = df_runs.style.map(_style_risk_level, subset=["risk_level"])
        st.dataframe(styled, width="stretch", hide_index=True)
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
            st.plotly_chart(fig, width="stretch")
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
            st.plotly_chart(fig, width="stretch")
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
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Sem dados de duracao.")
