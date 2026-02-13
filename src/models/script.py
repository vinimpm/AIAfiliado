from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class Script(Base):
    __tablename__ = "scripts"
    __table_args__ = (
        CheckConstraint(
            "platform IN ('tiktok', 'instagram', 'youtube')", name="chk_scripts_platform"
        ),
        CheckConstraint("variant IN ('A', 'B')", name="chk_scripts_variant"),
        CheckConstraint(
            "status IN ('draft', 'approved', 'rejected', 'used')", name="chk_scripts_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    trend_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("trends.id", ondelete="SET NULL"), nullable=True
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    hook: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    cta: Mapped[str] = mapped_column(Text, nullable=False)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    variant: Mapped[str] = mapped_column(String(5), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    product: Mapped["Product"] = relationship(back_populates="scripts")  # noqa: F821
    trend: Mapped["Trend | None"] = relationship(back_populates="scripts")  # noqa: F821
    videos: Mapped[list["Video"]] = relationship(back_populates="script", cascade="all, delete-orphan")  # noqa: F821  # fmt: skip

    def __repr__(self) -> str:
        return f"<Script {self.id} variant={self.variant} status={self.status}>"
