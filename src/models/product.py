from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price > 0", name="chk_products_price"),
        CheckConstraint("commission > 0", name="chk_products_commission"),
        CheckConstraint(
            "source_platform IN ('tiktok_shop', 'hotmart', 'monetizze', 'amazon', 'shopee')",
            name="chk_products_source_platform",
        ),
        CheckConstraint(
            "status IN ('pending', 'validated', 'rejected', 'retired')",
            name="chk_products_status",
        ),
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 100)", name="chk_products_score"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    source_platform: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    commission: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    affiliate_url: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    total_sales: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_revenue: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, server_default="0")
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    scripts: Mapped[list["Script"]] = relationship(back_populates="product", cascade="all, delete-orphan")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Product {self.title[:40]} ({self.source_platform})>"
