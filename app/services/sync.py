"""Idempotent upserts of Woo data into the CRM + metric refresh. Used by full sync and webhooks alike."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain.segmentation import compute_segment
from app.integrations.woo import WooClient, map_customer, map_order
from app.models import Customer, Order
from app.services.events import Event, EventBus

COUNTED_STATUSES = {"completed", "processing", "on-hold"}


def upsert_customer(db: Session, bus: EventBus, mapped: dict) -> Customer | None:
    if not mapped["email"]:
        return None
    cust = db.scalar(select(Customer).where(Customer.email == mapped["email"]))
    created = cust is None
    if created:
        cust = Customer(email=mapped["email"])
        db.add(cust)
    for k in ("woo_id", "first_name", "last_name", "phone", "city", "country"):
        if mapped.get(k):
            setattr(cust, k, mapped[k])
    if created and mapped.get("created_at"):
        cust.created_at = mapped["created_at"]
    db.flush()
    if created:
        bus.publish(db, Event("customer.created", cust.id))
    return cust


def refresh_metrics(db: Session, bus: EventBus, cust: Customer) -> None:
    counted = [o for o in cust.orders if o.status in COUNTED_STATUSES]
    cust.order_count = len(counted)
    cust.total_spent = round(sum(o.total for o in counted), 2)
    cust.last_order_at = max((o.created_at for o in counted), default=None)
    new_segment = compute_segment(cust.order_count, cust.total_spent, cust.last_order_at, utcnow())
    old = cust.segment
    cust.segment = new_segment
    db.flush()
    if old != new_segment and cust.order_count:
        bus.publish(db, Event("segment.changed", cust.id, data={"from": old, "to": new_segment}))


def upsert_order(db: Session, bus: EventBus, raw: dict) -> Order | None:
    m = map_order(raw)
    cust = None
    if m["woo_customer_id"]:
        cust = db.scalar(select(Customer).where(Customer.woo_id == m["woo_customer_id"]))
    if cust is None:  # guest checkout or customer not synced yet -> match/create by billing email
        cust = upsert_customer(db, bus, m["billing"])
    if cust is None:
        return None
    order = db.scalar(select(Order).where(Order.woo_id == m["woo_id"]))
    now = utcnow().isoformat()
    created = order is None
    old_status = None if created else order.status
    if created:
        order = Order(woo_id=m["woo_id"], customer_id=cust.id, created_at=m["created_at"] or utcnow(), history=[])
        db.add(order)
    order.number, order.status, order.total = m["number"], m["status"], m["total"]
    order.currency, order.items = m["currency"], m["items"]
    if created or old_status != order.status:
        order.history = [*order.history, {"status": order.status, "at": (m["created_at"].isoformat() if created and m["created_at"] else now)}]
    db.flush()
    db.refresh(cust)
    refresh_metrics(db, bus, cust)
    if created:
        bus.publish(db, Event("order.created", cust.id, order.id))
    if created or old_status != order.status:
        bus.publish(db, Event(f"order.status:{order.status}", cust.id, order.id))
    return order


def full_sync(db: Session, bus: EventBus, client: WooClient, *, quiet: bool = False) -> dict:
    """Pull everything from Woo. quiet=True suppresses events (initial import must not blast emails)."""
    if quiet:
        bus = EventBus()
    n_c = sum(1 for raw in client.customers() if upsert_customer(db, bus, map_customer(raw)))
    n_o = sum(1 for raw in client.orders() if upsert_order(db, bus, raw))
    db.commit()
    return {"customers": n_c, "orders": n_o}
