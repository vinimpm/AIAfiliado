import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add src to path so models can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.base import Base  # noqa: E402
from models.daily_run import DailyRun  # noqa: E402, F401
from models.trend import Trend  # noqa: E402, F401
from models.product import Product  # noqa: E402, F401
from models.script import Script  # noqa: E402, F401
from models.video import Video  # noqa: E402, F401
from models.publication import Publication  # noqa: E402, F401
from models.metric import Metric  # noqa: E402, F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Override sqlalchemy.url from environment variable if available
database_url = os.environ.get("DATABASE_URL")
if database_url:
    # Railway/Heroku use postgres:// but SQLAlchemy 2.0 requires postgresql://
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    config.set_main_option("sqlalchemy.url", database_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
