from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utcnow


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    woo_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(100), default="")
    last_name: Mapped[str] = mapped_column(String(100), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    city: Mapped[str] = mapped_column(String(100), default="")
    country: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # computed metrics (refreshed by services.metrics)
    order_count: Mapped[int] = mapped_column(Integer, default=0)
    total_spent: Mapped[float] = mapped_column(Float, default=0.0)
    last_order_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    segment: Mapped[str] = mapped_column(String(20), default="prospect", index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")

    orders: Mapped[list["Order"]] = relationship(back_populates="customer", order_by="Order.created_at.desc()")
    communications: Mapped[list["Communication"]] = relationship(
        back_populates="customer", order_by="Communication.created_at.desc()"
    )

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or self.email


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    woo_id: Mapped[int] = mapped_column(Integer, unique=True)
    number: Mapped[str] = mapped_column(String(50))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(10), default="INR")
    items: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    history: Mapped[list] = mapped_column(JSON, default=list)  # [{status, at}]

    customer: Mapped[Customer] = relationship(back_populates="orders")


class Workflow(Base):
    __tablename__ = "workflows"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    trigger: Mapped[str] = mapped_column(String(60), index=True)  # e.g. order.status:completed
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)  # {"min_total": 1000, "segment": "vip"}
    action: Mapped[str] = mapped_column(String(30), default="send_email")  # send_email | add_tag
    subject: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    tag: Mapped[str] = mapped_column(String(50), default="")
    enabled: Mapped[bool] = mapped_column(default=True)


class Communication(Base):
    __tablename__ = "communications"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_comm_dedupe"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("workflows.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(20), default="email")
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="sent")  # draft | sent | failed
    origin: Mapped[str] = mapped_column(String(20), default="workflow")  # workflow | agent | manual
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    customer: Mapped[Customer] = relationship(back_populates="communications")


class WebhookEvent(Base):
    """Idempotency ledger: Woo retries webhooks, so each delivery id is processed once."""

    __tablename__ = "webhook_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[str] = mapped_column(String(100), unique=True)
    topic: Mapped[str] = mapped_column(String(60))
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
