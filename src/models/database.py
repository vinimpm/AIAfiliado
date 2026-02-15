from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

_engine = None
_SessionLocal = None


def engine_factory(url: str | None = None):
    global _engine, _SessionLocal
    url = url or settings.DATABASE_URL
    # Railway/Heroku use postgres:// but SQLAlchemy 2.0 requires postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    connect_args: dict = {}
    if settings.DATABASE_SSL:
        connect_args["sslmode"] = "require"
    _engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        connect_args=connect_args,
    )
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_session() -> Session:
    global _engine, _SessionLocal
    if _SessionLocal is None:
        engine_factory()
    return _SessionLocal()


@contextmanager
def get_session_cm():
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
