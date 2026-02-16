"""Videos page — list generated videos with download links."""

from __future__ import annotations

from urllib.parse import urlparse

import boto3
import streamlit as st
from sqlalchemy.orm import Session

from app.config import settings
from dashboard.components.charts import pie_chart
from dashboard.components.filters import date_range_filter, status_filter
from dashboard.data import queries

_PRESIGNED_TTL = 3600  # 1 hour


def _generate_download_url(s3_url: str) -> str | None:
    """Generate a presigned download URL from an S3 URL."""
    if not s3_url:
        return None
    try:
        parsed = urlparse(s3_url)
        hostname = parsed.hostname or ""

        if ".s3." in hostname and ".amazonaws.com" in hostname:
            bucket = hostname.split(".s3.")[0]
            key = parsed.path.lstrip("/")
        elif settings.S3_ENDPOINT_URL and s3_url.startswith(settings.S3_ENDPOINT_URL):
            path_parts = parsed.path.lstrip("/").split("/", 1)
            bucket = path_parts[0] if len(path_parts) > 0 else settings.S3_BUCKET_NAME
            key = path_parts[1] if len(path_parts) > 1 else ""
        else:
            bucket = settings.S3_BUCKET_NAME
            key = parsed.path.lstrip("/")

        s3_kwargs: dict[str, str] = {
            "aws_access_key_id": settings.AWS_ACCESS_KEY_ID,
            "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY,
            "region_name": settings.AWS_REGION,
        }
        if settings.S3_ENDPOINT_URL:
            s3_kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL

        s3_client = boto3.client("s3", **s3_kwargs)
        return s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=_PRESIGNED_TTL,
        )
    except Exception:
        return None


def render(session: Session):
    st.header("Videos")

    # --- Filters ---
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        days = date_range_filter(key="videos_period")
    with col_f2:
        status = status_filter(
            statuses=["pending", "generating", "ready", "failed"],
            key="videos_status",
        )

    # --- KPIs ---
    df_by_status = queries.videos_by_status(session, days=days)
    if not df_by_status.empty:
        total = int(df_by_status["count"].sum())
        ready = int(
            df_by_status.loc[df_by_status["status"] == "ready", "count"].sum()
        )
        failed = int(
            df_by_status.loc[df_by_status["status"] == "failed", "count"].sum()
        )
        col_k1, col_k2, col_k3 = st.columns(3)
        col_k1.metric("Total Videos", total)
        col_k2.metric("Prontos", ready)
        col_k3.metric("Falhas", failed)

    st.divider()

    col_table, col_chart = st.columns([3, 1])

    with col_chart:
        st.subheader("Por Status")
        if not df_by_status.empty:
            fig = pie_chart(df_by_status, names="status", values="count")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sem dados.")

    # --- Videos Table ---
    with col_table:
        st.subheader("Lista de Videos")
        df_videos = queries.videos_table(session, status=status, days=days)

        if df_videos.empty:
            st.info("Nenhum video encontrado no periodo.")
            return

        st.caption(f"{len(df_videos)} videos encontrados")

        for _, row in df_videos.iterrows():
            video_id = row["id"]
            pub_status = queries.video_publication_status(session, video_id)

            with st.container(border=True):
                c1, c2, c3 = st.columns([3, 1, 1])

                with c1:
                    st.markdown(f"**#{video_id}** — {row['product']}")
                    hook_text = row["hook"][:80] + "..." if len(str(row["hook"])) > 80 else row["hook"]
                    st.caption(f"Hook: {hook_text}")

                with c2:
                    status_colors = {
                        "ready": ":green[READY]",
                        "pending": ":orange[PENDING]",
                        "generating": ":blue[GENERATING]",
                        "failed": ":red[FAILED]",
                    }
                    st.markdown(status_colors.get(row["status"], row["status"]))

                    pub_colors = {
                        "posted": ":green[Publicado]",
                        "scheduled": ":blue[Agendado]",
                        "uploading": ":blue[Enviando]",
                        "failed": ":red[Pub. Falhou]",
                        "nao publicado": ":orange[Nao publicado]",
                    }
                    st.markdown(pub_colors.get(pub_status, pub_status))

                with c3:
                    duration = row["duration"]
                    if duration:
                        st.caption(f"Duracao: {float(duration):.0f}s")
                    cost = row["cost_usd"]
                    if cost:
                        st.caption(f"Custo: ${float(cost):.2f}")

                    if row["status"] == "ready" and row["s3_url"]:
                        download_url = _generate_download_url(row["s3_url"])
                        if download_url:
                            st.link_button(
                                "Download",
                                download_url,
                                use_container_width=True,
                            )
