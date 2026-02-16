"""Read-only database queries for the dashboard.

All functions accept a SQLAlchemy Session and return plain dicts/lists
suitable for Streamlit display and Plotly charts.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session

from models.daily_run import DailyRun
from models.metric import Metric
from models.product import Product
from models.publication import Publication
from models.script import Script
from models.video import Video


def _cutoff(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


def kpi_totals(session: Session, days: int = 7) -> dict:
    """Aggregate KPIs for the overview page."""
    cutoff = _cutoff(days)

    videos = (
        session.query(func.count(Video.id))
        .filter(Video.created_at >= cutoff, Video.status == "ready")
        .scalar()
        or 0
    )

    publications = (
        session.query(func.count(Publication.id))
        .filter(Publication.posted_at >= cutoff, Publication.status == "POSTED")
        .scalar()
        or 0
    )

    metrics_agg = (
        session.query(
            func.coalesce(func.sum(Metric.views), 0),
            func.coalesce(func.sum(Metric.sales), 0),
            func.coalesce(func.sum(Metric.revenue), 0),
        )
        .filter(Metric.collected_at >= cutoff)
        .first()
    )

    avg_retention = (
        session.query(func.avg(Metric.retention_3s))
        .filter(Metric.collected_at >= cutoff, Metric.retention_3s.isnot(None))
        .scalar()
    )

    return {
        "videos": int(videos),
        "publications": int(publications),
        "views": int(metrics_agg[0]),
        "sales": int(metrics_agg[1]),
        "revenue": float(metrics_agg[2]),
        "avg_retention": float(avg_retention) if avg_retention else 0.0,
    }


def today_run(session: Session) -> dict | None:
    """Get today's DailyRun status."""
    run = session.query(DailyRun).filter(DailyRun.run_date == date.today()).first()
    if not run:
        return None
    return {
        "run_date": run.run_date,
        "risk_score": float(run.risk_score),
        "risk_level": run.risk_level,
        "posts_allowed": run.posts_allowed,
        "cooldown_minutes": run.cooldown_minutes,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def run_status_by_day(session: Session, days: int = 7) -> pd.DataFrame:
    """Count daily runs by status over the last N days."""
    cutoff = _cutoff(days).date()
    rows = (
        session.query(
            DailyRun.run_date,
            DailyRun.status,
        )
        .filter(DailyRun.run_date >= cutoff)
        .all()
    )
    if not rows:
        return pd.DataFrame(columns=["run_date", "status"])
    return pd.DataFrame(rows, columns=["run_date", "status"])


def alerts(session: Session) -> list[dict]:
    """Generate alert items for the overview page."""
    items = []
    run = today_run(session)
    if run and run["risk_level"] == "HIGH":
        items.append({"level": "error", "message": f"Risk level HIGH (score {run['risk_score']})"})
    if run and run["status"] == "failed":
        items.append({"level": "error", "message": "Today's pipeline run FAILED"})

    # Low retention alert
    recent_ret = (
        session.query(func.avg(Metric.retention_3s))
        .filter(Metric.collected_at >= _cutoff(3), Metric.retention_3s.isnot(None))
        .scalar()
    )
    if recent_ret is not None and float(recent_ret) < 0.3:
        items.append(
            {
                "level": "warning",
                "message": f"Avg retention 3s is low: {float(recent_ret):.1%}",
            }
        )
    return items


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def today_pipeline_summary(session: Session) -> dict:
    """Get a detailed summary of today's pipeline execution."""
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    products_active = (
        session.query(func.count(Product.id))
        .filter(Product.is_active.is_(True), Product.status == "validated")
        .scalar()
        or 0
    )

    scripts_today = (
        session.query(func.count(Script.id))
        .filter(Script.created_at >= today_start)
        .scalar()
        or 0
    )

    videos_today = (
        session.query(func.count(Video.id))
        .filter(Video.created_at >= today_start)
        .scalar()
        or 0
    )

    videos_ready = (
        session.query(func.count(Video.id))
        .filter(Video.created_at >= today_start, Video.status == "ready")
        .scalar()
        or 0
    )

    videos_failed = (
        session.query(func.count(Video.id))
        .filter(Video.created_at >= today_start, Video.status == "failed")
        .scalar()
        or 0
    )

    return {
        "products_active": int(products_active),
        "scripts_generated": int(scripts_today),
        "videos_total": int(videos_today),
        "videos_ready": int(videos_ready),
        "videos_failed": int(videos_failed),
    }


def daily_runs_table(session: Session, days: int = 30) -> pd.DataFrame:
    """All daily runs for the pipeline table."""
    cutoff = _cutoff(days).date()
    rows = (
        session.query(
            DailyRun.run_date,
            DailyRun.risk_score,
            DailyRun.risk_level,
            DailyRun.posts_allowed,
            DailyRun.cooldown_minutes,
            DailyRun.status,
            DailyRun.started_at,
            DailyRun.finished_at,
        )
        .filter(DailyRun.run_date >= cutoff)
        .order_by(DailyRun.run_date.desc())
        .all()
    )
    cols = [
        "run_date",
        "risk_score",
        "risk_level",
        "posts_allowed",
        "cooldown_minutes",
        "status",
        "started_at",
        "finished_at",
    ]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols)


def risk_score_trend(session: Session, days: int = 30) -> pd.DataFrame:
    """Risk score over time for line chart."""
    cutoff = _cutoff(days).date()
    rows = (
        session.query(DailyRun.run_date, DailyRun.risk_score)
        .filter(DailyRun.run_date >= cutoff)
        .order_by(DailyRun.run_date)
        .all()
    )
    if not rows:
        return pd.DataFrame(columns=["run_date", "risk_score"])
    return pd.DataFrame(rows, columns=["run_date", "risk_score"])


def success_rate_trend(session: Session, days: int = 30) -> pd.DataFrame:
    """Publications posted / posts_allowed per day."""
    cutoff = _cutoff(days).date()
    runs = (
        session.query(DailyRun.run_date, DailyRun.posts_allowed, DailyRun.id)
        .filter(DailyRun.run_date >= cutoff)
        .order_by(DailyRun.run_date)
        .all()
    )
    if not runs:
        return pd.DataFrame(columns=["run_date", "success_rate"])
    result = []
    for run_date, posts_allowed, run_id in runs:
        posted = (
            session.query(func.count(Publication.id))
            .filter(Publication.run_id == run_id, Publication.status == "POSTED")
            .scalar()
            or 0
        )
        rate = posted / posts_allowed if posts_allowed > 0 else 0.0
        result.append({"run_date": run_date, "success_rate": rate})
    return pd.DataFrame(result)


def run_duration_chart(session: Session, days: int = 30) -> pd.DataFrame:
    """Duration (minutes) of each completed run."""
    cutoff = _cutoff(days).date()
    rows = (
        session.query(DailyRun.run_date, DailyRun.started_at, DailyRun.finished_at)
        .filter(DailyRun.run_date >= cutoff, DailyRun.finished_at.isnot(None))
        .order_by(DailyRun.run_date)
        .all()
    )
    if not rows:
        return pd.DataFrame(columns=["run_date", "duration_min"])
    result = []
    for run_date, started, finished in rows:
        delta = (finished - started).total_seconds() / 60
        result.append({"run_date": run_date, "duration_min": round(delta, 1)})
    return pd.DataFrame(result)


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


def engagement_over_time(session: Session, days: int = 7) -> pd.DataFrame:
    """Views, likes, shares aggregated by day."""
    cutoff = _cutoff(days)
    rows = (
        session.query(
            func.date(Metric.collected_at).label("day"),
            func.sum(Metric.views).label("views"),
            func.sum(Metric.likes).label("likes"),
            func.sum(Metric.shares).label("shares"),
        )
        .filter(Metric.collected_at >= cutoff)
        .group_by(func.date(Metric.collected_at))
        .order_by("day")
        .all()
    )
    cols = ["day", "views", "likes", "shares"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols)


def retention_distribution(session: Session, days: int = 30) -> pd.DataFrame:
    """Retention 3s values for histogram."""
    cutoff = _cutoff(days)
    rows = (
        session.query(Metric.retention_3s)
        .filter(Metric.collected_at >= cutoff, Metric.retention_3s.isnot(None))
        .all()
    )
    if not rows:
        return pd.DataFrame(columns=["retention_3s"])
    return pd.DataFrame(rows, columns=["retention_3s"])


def funnel_data(session: Session, days: int = 30) -> dict:
    """Views -> Clicks -> Sales funnel."""
    cutoff = _cutoff(days)
    agg = (
        session.query(
            func.coalesce(func.sum(Metric.views), 0),
            func.coalesce(func.sum(Metric.clicks), 0),
            func.coalesce(func.sum(Metric.sales), 0),
        )
        .filter(Metric.collected_at >= cutoff)
        .first()
    )
    views, clicks, sales = int(agg[0]), int(agg[1]), int(agg[2])
    return {
        "views": views,
        "clicks": clicks,
        "sales": sales,
        "ctr": clicks / views if views > 0 else 0.0,
        "cvr": sales / clicks if clicks > 0 else 0.0,
    }


def ab_comparison(session: Session, days: int = 30) -> pd.DataFrame:
    """Compare A/B script variants by aggregated metrics."""
    cutoff = _cutoff(days)
    rows = (
        session.query(
            Script.variant,
            func.count(Publication.id).label("publications"),
            func.coalesce(func.sum(Metric.views), 0).label("views"),
            func.coalesce(func.sum(Metric.clicks), 0).label("clicks"),
            func.coalesce(func.sum(Metric.sales), 0).label("sales"),
            func.coalesce(func.sum(Metric.revenue), 0).label("revenue"),
            func.avg(Metric.retention_3s).label("avg_retention"),
        )
        .join(Video, Video.script_id == Script.id)
        .join(Publication, Publication.video_id == Video.id)
        .join(Metric, Metric.publication_id == Publication.id)
        .filter(Metric.collected_at >= cutoff)
        .group_by(Script.variant)
        .all()
    )
    cols = ["variant", "publications", "views", "clicks", "sales", "revenue", "avg_retention"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols)


def top_publications(session: Session, days: int = 30, limit: int = 10) -> pd.DataFrame:
    """Top N publications by views."""
    cutoff = _cutoff(days)
    rows = (
        session.query(
            Publication.id,
            Publication.platform,
            Publication.posted_at,
            func.coalesce(func.sum(Metric.views), 0).label("views"),
            func.coalesce(func.sum(Metric.likes), 0).label("likes"),
            func.coalesce(func.sum(Metric.sales), 0).label("sales"),
        )
        .join(Metric, Metric.publication_id == Publication.id)
        .filter(Metric.collected_at >= cutoff)
        .group_by(Publication.id, Publication.platform, Publication.posted_at)
        .order_by(func.sum(Metric.views).desc())
        .limit(limit)
        .all()
    )
    cols = ["pub_id", "platform", "posted_at", "views", "likes", "sales"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


def products_table(
    session: Session,
    platform: str | None = None,
    status: str | None = None,
) -> pd.DataFrame:
    """Full products table with optional filters."""
    q = session.query(
        Product.id,
        Product.title,
        Product.source_platform,
        Product.price,
        Product.commission,
        Product.category,
        Product.status,
        Product.score,
        Product.is_active,
        Product.total_sales,
        Product.total_revenue,
        Product.created_at,
    )
    if platform:
        q = q.filter(Product.source_platform == platform)
    if status:
        q = q.filter(Product.status == status)
    rows = q.order_by(Product.total_revenue.desc()).all()
    cols = [
        "id",
        "title",
        "source_platform",
        "price",
        "commission",
        "category",
        "status",
        "score",
        "is_active",
        "total_sales",
        "total_revenue",
        "created_at",
    ]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols)


def winners(session: Session) -> pd.DataFrame:
    """Active products (winners) with total revenue/sales."""
    rows = (
        session.query(
            Product.id,
            Product.title,
            Product.source_platform,
            Product.total_sales,
            Product.total_revenue,
            Product.score,
        )
        .filter(Product.is_active.is_(True))
        .order_by(Product.total_revenue.desc())
        .all()
    )
    cols = ["id", "title", "source_platform", "total_sales", "total_revenue", "score"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols)


def products_by_platform(session: Session) -> pd.DataFrame:
    """Product count by platform for pie chart."""
    rows = (
        session.query(
            Product.source_platform,
            func.count(Product.id).label("count"),
        )
        .group_by(Product.source_platform)
        .all()
    )
    if not rows:
        return pd.DataFrame(columns=["source_platform", "count"])
    return pd.DataFrame(rows, columns=["source_platform", "count"])


def top_products_by_revenue(session: Session, limit: int = 10) -> pd.DataFrame:
    """Top N products by total revenue."""
    rows = (
        session.query(
            Product.title,
            Product.source_platform,
            Product.total_revenue,
        )
        .order_by(Product.total_revenue.desc())
        .limit(limit)
        .all()
    )
    cols = ["title", "source_platform", "total_revenue"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------------------
# Financeiro
# ---------------------------------------------------------------------------


def financial_kpis(session: Session, days: int = 30) -> dict:
    """Cost, revenue, profit, ROI."""
    cutoff = _cutoff(days)
    cost = (
        session.query(func.coalesce(func.sum(Video.cost_usd), 0))
        .filter(Video.created_at >= cutoff)
        .scalar()
    )
    revenue = (
        session.query(func.coalesce(func.sum(Metric.revenue), 0))
        .filter(Metric.collected_at >= cutoff)
        .scalar()
    )
    cost_f = float(cost)
    revenue_f = float(revenue)
    profit = revenue_f - cost_f
    roi = (profit / cost_f * 100) if cost_f > 0 else 0.0
    return {
        "cost": cost_f,
        "revenue": revenue_f,
        "profit": profit,
        "roi": roi,
    }


def daily_cost_revenue(session: Session, days: int = 30) -> pd.DataFrame:
    """Cost and revenue by day for line chart."""
    cutoff = _cutoff(days)

    cost_rows = (
        session.query(
            func.date(Video.created_at).label("day"),
            func.coalesce(func.sum(Video.cost_usd), 0).label("cost"),
        )
        .filter(Video.created_at >= cutoff)
        .group_by(func.date(Video.created_at))
        .all()
    )

    revenue_rows = (
        session.query(
            func.date(Metric.collected_at).label("day"),
            func.coalesce(func.sum(Metric.revenue), 0).label("revenue"),
        )
        .filter(Metric.collected_at >= cutoff)
        .group_by(func.date(Metric.collected_at))
        .all()
    )

    cost_df = (
        pd.DataFrame(cost_rows, columns=["day", "cost"])
        if cost_rows
        else pd.DataFrame(columns=["day", "cost"])
    )
    rev_df = (
        pd.DataFrame(revenue_rows, columns=["day", "revenue"])
        if revenue_rows
        else pd.DataFrame(columns=["day", "revenue"])
    )

    if cost_df.empty and rev_df.empty:
        return pd.DataFrame(columns=["day", "cost", "revenue"])

    merged = pd.merge(cost_df, rev_df, on="day", how="outer").fillna(0).sort_values("day")
    return merged


def cumulative_pnl(session: Session, days: int = 30) -> pd.DataFrame:
    """Cumulative P&L over time."""
    df = daily_cost_revenue(session, days)
    if df.empty:
        return pd.DataFrame(columns=["day", "pnl"])
    df["daily_pnl"] = df["revenue"].astype(float) - df["cost"].astype(float)
    df["pnl"] = df["daily_pnl"].cumsum()
    return df[["day", "pnl"]]


def budget_usage_today(session: Session, daily_budget: float) -> dict:
    """Today's cost vs daily budget for gauge."""
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    cost = (
        session.query(func.coalesce(func.sum(Video.cost_usd), 0))
        .filter(Video.created_at >= today_start)
        .scalar()
    )
    cost_f = float(cost)
    return {
        "used": cost_f,
        "budget": daily_budget,
        "pct": (cost_f / daily_budget * 100) if daily_budget > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# Account Health
# ---------------------------------------------------------------------------


def current_health(session: Session) -> dict | None:
    """Latest risk score and level."""
    run = session.query(DailyRun).order_by(DailyRun.run_date.desc()).first()
    if not run:
        return None
    return {
        "risk_score": float(run.risk_score),
        "risk_level": run.risk_level,
        "posts_allowed": run.posts_allowed,
        "cooldown_minutes": run.cooldown_minutes,
        "run_date": run.run_date,
    }


def health_history(session: Session, days: int = 30) -> pd.DataFrame:
    """Risk score history for line chart."""
    cutoff = _cutoff(days).date()
    rows = (
        session.query(
            DailyRun.run_date,
            DailyRun.risk_score,
            DailyRun.risk_level,
        )
        .filter(DailyRun.run_date >= cutoff)
        .order_by(DailyRun.run_date)
        .all()
    )
    cols = ["run_date", "risk_score", "risk_level"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols)


def posts_allowed_vs_used(session: Session, days: int = 30) -> pd.DataFrame:
    """Posts allowed vs actually posted per day."""
    cutoff = _cutoff(days).date()
    runs = (
        session.query(DailyRun.run_date, DailyRun.posts_allowed, DailyRun.id)
        .filter(DailyRun.run_date >= cutoff)
        .order_by(DailyRun.run_date)
        .all()
    )
    if not runs:
        return pd.DataFrame(columns=["run_date", "allowed", "used"])
    result = []
    for run_date, allowed, run_id in runs:
        used = (
            session.query(func.count(Publication.id))
            .filter(Publication.run_id == run_id, Publication.status == "POSTED")
            .scalar()
            or 0
        )
        result.append({"run_date": run_date, "allowed": allowed, "used": used})
    return pd.DataFrame(result)


# ---------------------------------------------------------------------------
# Videos
# ---------------------------------------------------------------------------


def videos_table(
    session: Session,
    status: str | None = None,
    days: int = 30,
) -> pd.DataFrame:
    """All videos with product title and publication status."""
    cutoff = _cutoff(days)
    q = (
        session.query(
            Video.id,
            Product.title.label("product"),
            Script.hook,
            Video.status,
            Video.duration,
            Video.cost_usd,
            Video.s3_url,
            Video.created_at,
        )
        .join(Script, Script.id == Video.script_id)
        .join(Product, Product.id == Script.product_id)
        .filter(Video.created_at >= cutoff)
    )
    if status:
        q = q.filter(Video.status == status)
    rows = q.order_by(Video.created_at.desc()).all()
    cols = ["id", "product", "hook", "status", "duration", "cost_usd", "s3_url", "created_at"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols)


def video_publication_status(session: Session, video_id: int) -> str:
    """Check if a video has been published."""
    pub = (
        session.query(Publication.status)
        .filter(Publication.video_id == video_id)
        .first()
    )
    if not pub:
        return "nao publicado"
    return pub[0].lower()


def videos_by_status(session: Session, days: int = 30) -> pd.DataFrame:
    """Count videos by status for pie chart."""
    cutoff = _cutoff(days)
    rows = (
        session.query(
            Video.status,
            func.count(Video.id).label("count"),
        )
        .filter(Video.created_at >= cutoff)
        .group_by(Video.status)
        .all()
    )
    if not rows:
        return pd.DataFrame(columns=["status", "count"])
    return pd.DataFrame(rows, columns=["status", "count"])


def cooldown_trend(session: Session, days: int = 30) -> pd.DataFrame:
    """Cooldown minutes over time."""
    cutoff = _cutoff(days).date()
    rows = (
        session.query(DailyRun.run_date, DailyRun.cooldown_minutes)
        .filter(DailyRun.run_date >= cutoff)
        .order_by(DailyRun.run_date)
        .all()
    )
    if not rows:
        return pd.DataFrame(columns=["run_date", "cooldown_minutes"])
    return pd.DataFrame(rows, columns=["run_date", "cooldown_minutes"])
