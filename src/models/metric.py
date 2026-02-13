from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class Metric(Base):
    __tablename__ = "metrics"
    __table_args__ = (
        CheckConstraint("views >= 0", name="chk_metrics_views"),
        CheckConstraint("likes >= 0", name="chk_metrics_likes"),
        CheckConstraint("comments >= 0", name="chk_metrics_comments"),
        CheckConstraint("shares >= 0", name="chk_metrics_shares"),
        CheckConstraint(
            "retention_3s IS NULL OR (retention_3s >= 0 AND retention_3s <= 1)",
            name="chk_metrics_retention_3s",
        ),
        CheckConstraint("clicks >= 0", name="chk_metrics_clicks"),
        CheckConstraint("sales >= 0", name="chk_metrics_sales"),
        CheckConstraint("revenue >= 0", name="chk_metrics_revenue"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    publication_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("publications.id", ondelete="CASCADE"), nullable=False
    )
    views: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    likes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    comments: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    shares: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    retention_3s: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sales: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    revenue: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, server_default="0")
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    publication: Mapped["Publication"] = relationship(back_populates="metrics")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Metric pub={self.publication_id} views={self.views} sales={self.sales}>"
