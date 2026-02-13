from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Integer, JSON, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class DailyRun(Base):
    __tablename__ = "daily_runs"
    __table_args__ = (
        CheckConstraint("risk_score >= 0 AND risk_score <= 100", name="chk_daily_runs_risk_score"),
        CheckConstraint(
            "risk_level IN ('LOW', 'MEDIUM', 'HIGH')", name="chk_daily_runs_risk_level"
        ),
        CheckConstraint("posts_allowed >= 0", name="chk_daily_runs_posts_allowed"),
        CheckConstraint("cooldown_minutes >= 0", name="chk_daily_runs_cooldown_minutes"),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'paused')",
            name="chk_daily_runs_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    risk_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(10), nullable=False)
    posts_allowed: Mapped[int] = mapped_column(Integer, nullable=False)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    limits_json: Mapped[dict | None] = mapped_column(JSON, server_default="{}")

    # Relationships
    trends: Mapped[list["Trend"]] = relationship(back_populates="run", cascade="all, delete-orphan")  # noqa: F821
    publications: Mapped[list["Publication"]] = relationship(back_populates="run")  # noqa: F821

    def __repr__(self) -> str:
        return f"<DailyRun {self.run_date} {self.risk_level}>"
