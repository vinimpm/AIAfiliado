"""Pytest fixtures for the AIAfiliado test suite.

Provides an in-memory SQLite database, session management,
and pre-built model instances for each core entity.
"""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from models.base import Base
from models.daily_run import DailyRun
from models.metric import Metric
from models.product import Product
from models.publication import Publication
from models.script import Script
from models.trend import Trend
from models.video import Video


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_engine():
    """Create a SQLite in-memory engine with all tables."""
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
def db_session(db_engine):
    """Yield a transactional session that rolls back after each test."""
    SessionLocal = sessionmaker(bind=db_engine, expire_on_commit=False)
    session: Session = SessionLocal()
    yield session
    session.rollback()
    session.close()


# ---------------------------------------------------------------------------
# Model fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_daily_run(db_session):
    """Create and return a DailyRun record persisted in the test database."""
    run = DailyRun(
        run_date=date.today(),
        risk_score=20,
        risk_level="LOW",
        posts_allowed=3,
        cooldown_minutes=120,
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.flush()
    return run


@pytest.fixture()
def sample_product(db_session):
    """Create and return a Product record persisted in the test database."""
    product = Product(
        source="test",
        source_platform="amazon",
        title="Test Product",
        price=59.90,
        commission=10.0,
        affiliate_url="https://test.com/aff",
        category="beauty",
        status="validated",
        is_active=True,
        score=75.0,
        total_sales=0,
        total_revenue=0,
        validated_at=datetime.now(timezone.utc),
    )
    db_session.add(product)
    db_session.flush()
    return product


@pytest.fixture()
def sample_trend(db_session, sample_daily_run):
    """Create and return a Trend record linked to the sample DailyRun."""
    trend = Trend(
        run_id=sample_daily_run.id,
        name="skincare coreano",
        score=85.0,
        window_days=7,
        source="tiktok_cc",
        category="beauty",
        platform="tiktok",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(trend)
    db_session.flush()
    return trend


@pytest.fixture()
def sample_script(db_session, sample_product, sample_trend):
    """Create and return a Script record linked to the sample Product and Trend."""
    script = Script(
        product_id=sample_product.id,
        trend_id=sample_trend.id,
        platform="tiktok",
        hook="Voce sabia que skincare coreano mudou tudo?",
        body="Esse produto viralizou no TikTok e agora todo mundo quer testar.",
        cta="Link na bio para conferir!",
        caption="#skincare #coreano #beleza #tiktok",
        hash="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        variant="A",
        status="approved",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(script)
    db_session.flush()
    return script


@pytest.fixture()
def sample_video(db_session, sample_script):
    """Create and return a Video record linked to the sample Script."""
    video = Video(
        script_id=sample_script.id,
        provider="heygen",
        duration=30.0,
        s3_url="https://s3.amazonaws.com/bucket/video_test.mp4",
        hash="f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5",
        cost_usd=0.50,
        status="ready",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(video)
    db_session.flush()
    return video


@pytest.fixture()
def sample_publication(db_session, sample_video, sample_daily_run):
    """Create and return a Publication record linked to the sample Video and DailyRun."""
    publication = Publication(
        video_id=sample_video.id,
        run_id=sample_daily_run.id,
        platform="tiktok",
        status="POSTED",
        external_id="tiktok_12345",
        posted_at=datetime.now(timezone.utc),
    )
    db_session.add(publication)
    db_session.flush()
    return publication
