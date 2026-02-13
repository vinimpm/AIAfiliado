from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (
        CheckConstraint(
            "platform IN ('tiktok', 'instagram', 'youtube')",
            name="chk_publications_platform",
        ),
        CheckConstraint(
            "status IN ('SCHEDULED', 'UPLOADING', 'POSTED', 'FAILED')",
            name="chk_publications_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("daily_runs.id", ondelete="SET NULL"), nullable=True
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="SCHEDULED")
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    video: Mapped["Video"] = relationship(back_populates="publications")  # noqa: F821
    run: Mapped["DailyRun | None"] = relationship(back_populates="publications")  # noqa: F821
    metrics: Mapped[list["Metric"]] = relationship(back_populates="publication", cascade="all, delete-orphan")  # noqa: F821  # fmt: skip

    def __repr__(self) -> str:
        return f"<Publication {self.id} {self.platform} {self.status}>"
