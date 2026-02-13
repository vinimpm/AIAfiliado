from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (
        CheckConstraint(
            "duration IS NULL OR duration > 0", name="chk_videos_duration"
        ),
        CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0", name="chk_videos_cost_usd"
        ),
        CheckConstraint(
            "status IN ('pending', 'generating', 'ready', 'failed')",
            name="chk_videos_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    script_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False, server_default="heygen")
    duration: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    s3_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    heygen_job_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    script: Mapped["Script"] = relationship(back_populates="videos")  # noqa: F821
    publications: Mapped[list["Publication"]] = relationship(back_populates="video", cascade="all, delete-orphan")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Video {self.id} status={self.status}>"
