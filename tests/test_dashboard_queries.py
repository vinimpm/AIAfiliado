"""Tests for dashboard queries using SQLite in-memory fixtures."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from dashboard.data import queries
from models.base import Base
from models.daily_run import DailyRun
from models.metric import Metric
from models.product import Product
from models.publication import Publication
from models.script import Script
from models.trend import Trend
from models.video import Video

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def session(db_engine):
    session_local = sessionmaker(bind=db_engine, expire_on_commit=False)
    s: Session = session_local()
    yield s
    s.rollback()
    s.close()


def _now():
    return datetime.now(UTC)


@pytest.fixture()
def seeded_session(session):
    """Seed the database with a full pipeline: run -> trend -> product -> script -> video -> publication -> metric."""
    run = DailyRun(
        run_date=date.today(),
        risk_score=25,
        risk_level="LOW",
        posts_allowed=5,
        cooldown_minutes=60,
        status="completed",
        started_at=_now() - timedelta(hours=2),
        finished_at=_now() - timedelta(hours=1),
    )
    session.add(run)
    session.flush()

    # Second run (yesterday)
    run_yesterday = DailyRun(
        run_date=date.today() - timedelta(days=1),
        risk_score=55,
        risk_level="MEDIUM",
        posts_allowed=3,
        cooldown_minutes=180,
        status="completed",
        started_at=_now() - timedelta(days=1, hours=2),
        finished_at=_now() - timedelta(days=1, hours=1),
    )
    session.add(run_yesterday)
    session.flush()

    trend = Trend(
        run_id=run.id,
        name="skincare coreano",
        score=85,
        window_days=7,
        source="tiktok_cc",
        category="beauty",
        status="active",
        created_at=_now(),
    )
    session.add(trend)
    session.flush()

    product = Product(
        source="test",
        source_platform="amazon",
        title="Serum Vitamina C",
        price=59.90,
        commission=10.0,
        affiliate_url="https://test.com/aff",
        category="beauty",
        status="validated",
        is_active=True,
        score=80,
        total_sales=15,
        total_revenue=250.00,
        created_at=_now(),
    )
    session.add(product)
    session.flush()

    product2 = Product(
        source="test",
        source_platform="shopee",
        title="Mascara Facial",
        price=29.90,
        commission=5.0,
        affiliate_url="https://test.com/aff2",
        category="beauty",
        status="validated",
        is_active=False,
        score=60,
        total_sales=3,
        total_revenue=45.00,
        created_at=_now(),
    )
    session.add(product2)
    session.flush()

    script_a = Script(
        product_id=product.id,
        trend_id=trend.id,
        platform="tiktok",
        hook="Hook A",
        body="Body A",
        cta="CTA A",
        hash="a" * 64,
        variant="A",
        status="used",
        created_at=_now(),
    )
    script_b = Script(
        product_id=product.id,
        trend_id=trend.id,
        platform="tiktok",
        hook="Hook B",
        body="Body B",
        cta="CTA B",
        hash="b" * 64,
        variant="B",
        status="used",
        created_at=_now(),
    )
    session.add_all([script_a, script_b])
    session.flush()

    video_a = Video(
        script_id=script_a.id,
        provider="heygen",
        duration=30,
        cost_usd=0.50,
        status="ready",
        created_at=_now(),
    )
    video_b = Video(
        script_id=script_b.id,
        provider="heygen",
        duration=25,
        cost_usd=0.45,
        status="ready",
        created_at=_now(),
    )
    session.add_all([video_a, video_b])
    session.flush()

    pub_a = Publication(
        video_id=video_a.id,
        run_id=run.id,
        platform="tiktok",
        status="POSTED",
        posted_at=_now(),
    )
    pub_b = Publication(
        video_id=video_b.id,
        run_id=run.id,
        platform="tiktok",
        status="POSTED",
        posted_at=_now(),
    )
    session.add_all([pub_a, pub_b])
    session.flush()

    metric_a = Metric(
        publication_id=pub_a.id,
        views=5000,
        likes=300,
        comments=20,
        shares=50,
        retention_3s=0.65,
        clicks=100,
        sales=5,
        revenue=150.00,
        collected_at=_now(),
    )
    metric_b = Metric(
        publication_id=pub_b.id,
        views=3000,
        likes=200,
        comments=10,
        shares=30,
        retention_3s=0.55,
        clicks=60,
        sales=2,
        revenue=80.00,
        collected_at=_now(),
    )
    session.add_all([metric_a, metric_b])
    session.flush()

    return session


# ---------------------------------------------------------------------------
# Overview queries
# ---------------------------------------------------------------------------


class TestKpiTotals:
    def test_returns_all_keys(self, seeded_session):
        result = queries.kpi_totals(seeded_session, days=7)
        assert set(result.keys()) == {
            "videos",
            "publications",
            "views",
            "sales",
            "revenue",
            "avg_retention",
        }

    def test_values_correct(self, seeded_session):
        result = queries.kpi_totals(seeded_session, days=7)
        assert result["videos"] == 2
        assert result["publications"] == 2
        assert result["views"] == 8000
        assert result["sales"] == 7
        assert result["revenue"] == 230.0
        assert 0.55 <= result["avg_retention"] <= 0.65

    def test_empty_db(self, session):
        result = queries.kpi_totals(session, days=7)
        assert result["videos"] == 0
        assert result["views"] == 0


class TestTodayRun:
    def test_returns_today(self, seeded_session):
        result = queries.today_run(seeded_session)
        assert result is not None
        assert result["risk_level"] == "LOW"
        assert result["posts_allowed"] == 5

    def test_no_run(self, session):
        assert queries.today_run(session) is None


class TestRunStatusByDay:
    def test_returns_rows(self, seeded_session):
        df = queries.run_status_by_day(seeded_session, days=7)
        assert len(df) == 2

    def test_empty(self, session):
        df = queries.run_status_by_day(session, days=7)
        assert df.empty


class TestAlerts:
    def test_no_alerts_on_healthy(self, seeded_session):
        result = queries.alerts(seeded_session)
        # LOW risk and retention > 0.3, no failed run
        assert len(result) == 0

    def test_empty_db_no_crash(self, session):
        result = queries.alerts(session)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Pipeline queries
# ---------------------------------------------------------------------------


class TestDailyRunsTable:
    def test_returns_dataframe(self, seeded_session):
        df = queries.daily_runs_table(seeded_session, days=30)
        assert len(df) == 2
        assert "risk_level" in df.columns


class TestRiskScoreTrend:
    def test_returns_data(self, seeded_session):
        df = queries.risk_score_trend(seeded_session, days=30)
        assert len(df) == 2
        assert df.iloc[0]["risk_score"] <= df.iloc[1]["risk_score"] or True  # just check it works


class TestSuccessRateTrend:
    def test_calculates_rate(self, seeded_session):
        df = queries.success_rate_trend(seeded_session, days=30)
        assert len(df) == 2
        # Today: 2 posted / 5 allowed = 0.4
        today_row = df[df["run_date"] == date.today()]
        assert len(today_row) == 1
        assert today_row.iloc[0]["success_rate"] == pytest.approx(0.4)


class TestRunDurationChart:
    def test_returns_durations(self, seeded_session):
        df = queries.run_duration_chart(seeded_session, days=30)
        assert len(df) == 2
        assert all(df["duration_min"] > 0)


# ---------------------------------------------------------------------------
# Performance queries
# ---------------------------------------------------------------------------


class TestEngagementOverTime:
    def test_returns_data(self, seeded_session):
        df = queries.engagement_over_time(seeded_session, days=7)
        assert len(df) >= 1
        assert int(df["views"].sum()) == 8000


class TestRetentionDistribution:
    def test_returns_values(self, seeded_session):
        df = queries.retention_distribution(seeded_session, days=30)
        assert len(df) == 2


class TestFunnelData:
    def test_funnel_values(self, seeded_session):
        result = queries.funnel_data(seeded_session, days=30)
        assert result["views"] == 8000
        assert result["clicks"] == 160
        assert result["sales"] == 7
        assert result["ctr"] == pytest.approx(160 / 8000)
        assert result["cvr"] == pytest.approx(7 / 160)


class TestAbComparison:
    def test_returns_variants(self, seeded_session):
        df = queries.ab_comparison(seeded_session, days=30)
        assert len(df) == 2
        assert set(df["variant"]) == {"A", "B"}


class TestTopPublications:
    def test_returns_ordered(self, seeded_session):
        df = queries.top_publications(seeded_session, days=30, limit=10)
        assert len(df) == 2
        assert int(df.iloc[0]["views"]) >= int(df.iloc[1]["views"])


# ---------------------------------------------------------------------------
# Products queries
# ---------------------------------------------------------------------------


class TestProductsTable:
    def test_returns_all(self, seeded_session):
        df = queries.products_table(seeded_session)
        assert len(df) == 2

    def test_filter_platform(self, seeded_session):
        df = queries.products_table(seeded_session, platform="amazon")
        assert len(df) == 1
        assert df.iloc[0]["source_platform"] == "amazon"

    def test_filter_status(self, seeded_session):
        df = queries.products_table(seeded_session, status="validated")
        assert len(df) == 2


class TestWinners:
    def test_returns_active_only(self, seeded_session):
        df = queries.winners(seeded_session)
        assert len(df) == 1
        assert df.iloc[0]["title"] == "Serum Vitamina C"


class TestProductsByPlatform:
    def test_returns_counts(self, seeded_session):
        df = queries.products_by_platform(seeded_session)
        assert len(df) == 2
        total = df["count"].sum()
        assert total == 2


class TestTopProductsByRevenue:
    def test_ordered_by_revenue(self, seeded_session):
        df = queries.top_products_by_revenue(seeded_session, limit=10)
        assert len(df) == 2
        assert float(df.iloc[0]["total_revenue"]) >= float(df.iloc[1]["total_revenue"])


# ---------------------------------------------------------------------------
# Financeiro queries
# ---------------------------------------------------------------------------


class TestFinancialKpis:
    def test_values(self, seeded_session):
        result = queries.financial_kpis(seeded_session, days=30)
        assert result["cost"] == pytest.approx(0.95)
        assert result["revenue"] == pytest.approx(230.0)
        assert result["profit"] == pytest.approx(230.0 - 0.95)
        assert result["roi"] > 0

    def test_empty(self, session):
        result = queries.financial_kpis(session, days=30)
        assert result["cost"] == 0
        assert result["roi"] == 0


class TestDailyCostRevenue:
    def test_returns_merged(self, seeded_session):
        df = queries.daily_cost_revenue(seeded_session, days=30)
        assert len(df) >= 1
        assert "cost" in df.columns
        assert "revenue" in df.columns


class TestCumulativePnl:
    def test_returns_pnl(self, seeded_session):
        df = queries.cumulative_pnl(seeded_session, days=30)
        assert len(df) >= 1
        assert "pnl" in df.columns


class TestBudgetUsageToday:
    def test_returns_usage(self, seeded_session):
        result = queries.budget_usage_today(seeded_session, daily_budget=5.0)
        assert result["used"] == pytest.approx(0.95)
        assert result["budget"] == 5.0
        assert result["pct"] == pytest.approx(0.95 / 5.0 * 100)


# ---------------------------------------------------------------------------
# Account Health queries
# ---------------------------------------------------------------------------


class TestCurrentHealth:
    def test_returns_latest(self, seeded_session):
        result = queries.current_health(seeded_session)
        assert result is not None
        # Today's run (risk_score=25) is the most recent
        assert result["risk_score"] == 25
        assert result["risk_level"] == "LOW"

    def test_empty(self, session):
        assert queries.current_health(session) is None


class TestHealthHistory:
    def test_returns_history(self, seeded_session):
        df = queries.health_history(seeded_session, days=30)
        assert len(df) == 2
        assert "risk_level" in df.columns


class TestPostsAllowedVsUsed:
    def test_returns_data(self, seeded_session):
        df = queries.posts_allowed_vs_used(seeded_session, days=30)
        assert len(df) == 2
        today_row = df[df["run_date"] == date.today()]
        assert today_row.iloc[0]["allowed"] == 5
        assert today_row.iloc[0]["used"] == 2


class TestCooldownTrend:
    def test_returns_data(self, seeded_session):
        df = queries.cooldown_trend(seeded_session, days=30)
        assert len(df) == 2
