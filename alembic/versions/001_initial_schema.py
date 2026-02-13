"""Initial schema - all 7 tables

Revision ID: 001
Revises:
Create Date: 2025-03-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === daily_runs ===
    op.create_table(
        "daily_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("risk_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("risk_level", sa.String(10), nullable=False),
        sa.Column("posts_allowed", sa.Integer(), nullable=False),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("limits_json", sa.JSON(), server_default="{}"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_date"),
        sa.CheckConstraint("risk_score >= 0 AND risk_score <= 100"),
        sa.CheckConstraint("risk_level IN ('LOW', 'MEDIUM', 'HIGH')"),
        sa.CheckConstraint("posts_allowed >= 0"),
        sa.CheckConstraint("cooldown_minutes >= 0"),
        sa.CheckConstraint("status IN ('running', 'completed', 'failed', 'paused')"),
    )
    op.create_index("idx_daily_runs_date", "daily_runs", ["run_date"], unique=True)
    op.create_index("idx_daily_runs_status", "daily_runs", ["status"])

    # === trends ===
    op.create_table(
        "trends",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("score", sa.Numeric(5, 2), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("platform", sa.String(50), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["daily_runs.id"], ondelete="CASCADE"),
        sa.CheckConstraint("score >= 0 AND score <= 100"),
        sa.CheckConstraint("window_days > 0"),
        sa.CheckConstraint("source IN ('tiktok_cc', 'tiktok_shop', 'google_trends')"),
        sa.CheckConstraint("status IN ('active', 'expired', 'discarded')"),
    )
    op.create_index("idx_trends_run_id", "trends", ["run_id"])
    op.create_index("idx_trends_status", "trends", ["status"])
    op.create_index("idx_trends_score", "trends", [sa.text("score DESC")])
    op.create_index("idx_trends_source", "trends", ["source"])
    op.create_index("idx_trends_created_at", "trends", [sa.text("created_at DESC")])

    # === products ===
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("source_platform", sa.String(50), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("commission", sa.Numeric(10, 2), nullable=False),
        sa.Column("affiliate_url", sa.Text(), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("score", sa.Numeric(5, 2), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("total_sales", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_revenue", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("price > 0"),
        sa.CheckConstraint("commission > 0"),
        sa.CheckConstraint(
            "source_platform IN ('tiktok_shop', 'hotmart', 'monetizze', 'amazon', 'shopee')"
        ),
        sa.CheckConstraint("status IN ('pending', 'validated', 'rejected', 'retired')"),
        sa.CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)"),
    )
    op.create_index("idx_products_source_platform", "products", ["source_platform"])
    op.create_index("idx_products_status", "products", ["status"])
    op.create_index(
        "idx_products_is_active",
        "products",
        ["is_active"],
        postgresql_where=sa.text("is_active = true"),
    )
    op.create_index("idx_products_score", "products", [sa.text("score DESC NULLS LAST")])
    op.create_index("idx_products_total_sales", "products", [sa.text("total_sales DESC")])
    op.create_index(
        "idx_products_last_used_at", "products", [sa.text("last_used_at DESC NULLS LAST")]
    )
    op.create_index(
        "idx_products_active_winners",
        "products",
        ["is_active", sa.text("total_sales DESC")],
        postgresql_where=sa.text("is_active = true AND total_sales > 0"),
    )

    # === scripts ===
    op.create_table(
        "scripts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("trend_id", sa.Integer(), nullable=True),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("hook", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("cta", sa.Text(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("variant", sa.String(5), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trend_id"], ["trends.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("hash"),
        sa.CheckConstraint("platform IN ('tiktok', 'instagram', 'youtube')"),
        sa.CheckConstraint("variant IN ('A', 'B')"),
        sa.CheckConstraint("status IN ('draft', 'approved', 'rejected', 'used')"),
    )
    op.create_index("idx_scripts_hash", "scripts", ["hash"], unique=True)
    op.create_index("idx_scripts_product_id", "scripts", ["product_id"])
    op.create_index("idx_scripts_trend_id", "scripts", ["trend_id"])
    op.create_index("idx_scripts_status", "scripts", ["status"])
    op.create_index("idx_scripts_variant", "scripts", ["variant"])

    # === videos ===
    op.create_table(
        "videos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("script_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False, server_default="heygen"),
        sa.Column("duration", sa.Numeric(6, 2), nullable=True),
        sa.Column("s3_url", sa.Text(), nullable=True),
        sa.Column("hash", sa.String(64), nullable=True),
        sa.Column("cost_usd", sa.Numeric(8, 4), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("heygen_job_id", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["script_id"], ["scripts.id"], ondelete="CASCADE"),
        sa.CheckConstraint("duration IS NULL OR duration > 0"),
        sa.CheckConstraint("cost_usd IS NULL OR cost_usd >= 0"),
        sa.CheckConstraint("status IN ('pending', 'generating', 'ready', 'failed')"),
    )
    op.create_index(
        "idx_videos_hash",
        "videos",
        ["hash"],
        unique=True,
        postgresql_where=sa.text("hash IS NOT NULL"),
    )
    op.create_index("idx_videos_script_id", "videos", ["script_id"])
    op.create_index("idx_videos_status", "videos", ["status"])
    op.create_index(
        "idx_videos_heygen_job_id",
        "videos",
        ["heygen_job_id"],
        postgresql_where=sa.text("heygen_job_id IS NOT NULL"),
    )

    # === publications ===
    op.create_table(
        "publications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("video_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="SCHEDULED"),
        sa.Column("external_id", sa.String(200), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["daily_runs.id"], ondelete="SET NULL"),
        sa.CheckConstraint("platform IN ('tiktok', 'instagram', 'youtube')"),
        sa.CheckConstraint("status IN ('SCHEDULED', 'UPLOADING', 'POSTED', 'FAILED')"),
    )
    op.create_index("idx_publications_video_id", "publications", ["video_id"])
    op.create_index("idx_publications_run_id", "publications", ["run_id"])
    op.create_index("idx_publications_platform", "publications", ["platform"])
    op.create_index("idx_publications_status", "publications", ["status"])
    op.create_index("idx_publications_scheduled_at", "publications", ["scheduled_at"])
    op.create_index(
        "idx_publications_posted_at", "publications", [sa.text("posted_at DESC NULLS LAST")]
    )
    op.create_index(
        "idx_publications_external_id",
        "publications",
        ["external_id"],
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    # === metrics ===
    op.create_table(
        "metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("publication_id", sa.Integer(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("likes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shares", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retention_3s", sa.Numeric(5, 4), nullable=True),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sales", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revenue", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="CASCADE"),
        sa.CheckConstraint("views >= 0"),
        sa.CheckConstraint("likes >= 0"),
        sa.CheckConstraint("comments >= 0"),
        sa.CheckConstraint("shares >= 0"),
        sa.CheckConstraint("retention_3s IS NULL OR (retention_3s >= 0 AND retention_3s <= 1)"),
        sa.CheckConstraint("clicks >= 0"),
        sa.CheckConstraint("sales >= 0"),
        sa.CheckConstraint("revenue >= 0"),
    )
    op.create_index("idx_metrics_publication_id", "metrics", ["publication_id"])
    op.create_index("idx_metrics_collected_at", "metrics", [sa.text("collected_at DESC")])
    op.create_index(
        "idx_metrics_pub_collected",
        "metrics",
        ["publication_id", sa.text("collected_at DESC")],
    )


def downgrade() -> None:
    op.drop_table("metrics")
    op.drop_table("publications")
    op.drop_table("videos")
    op.drop_table("scripts")
    op.drop_table("products")
    op.drop_table("trends")
    op.drop_table("daily_runs")
