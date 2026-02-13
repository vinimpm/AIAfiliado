from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class Trend(Base):
    __tablename__ = "trends"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 100", name="chk_trends_score"),
        CheckConstraint("window_days > 0", name="chk_trends_window_days"),
        CheckConstraint(
            "source IN ('tiktok_cc', 'tiktok_shop', 'google_trends')",
            name="chk_trends_source",
        ),
        CheckConstraint("status IN ('active', 'expired', 'discarded')", name="chk_trends_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("daily_runs.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    run: Mapped["DailyRun"] = relationship(back_populates="trends")  # noqa: F821
    scripts: Mapped[list["Script"]] = relationship(back_populates="trend")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Trend {self.name} score={self.score}>"
