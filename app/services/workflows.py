"""Workflow engine: trigger -> conditions -> action, with templating and dedupe. Subscribes to the EventBus."""
import re
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Communication, Customer, Order, Workflow
from app.services.events import Event


class EmailSender(Protocol):
    """Strategy: how a message is actually delivered."""

    def send(self, to: str, subject: str, body: str) -> bool: ...


class RecordingSender:
    """Default demo sender: delivery is the communication log itself (swap for SMTP/SendGrid in prod)."""

    def send(self, to: str, subject: str, body: str) -> bool:
        return True


def render(template: str, customer: Customer, order: Order | None = None, extra: dict | None = None) -> str:
    ctx = {"first_name": customer.first_name or "there", "name": customer.name, "email": customer.email,
           "total_spent": f"{customer.total_spent:.0f}", "segment": customer.segment.replace("_", " "), **(extra or {})}
    if order:
        ctx.update(order_number=order.number, order_total=f"{order.total:.0f}", order_status=order.status,
                   currency=order.currency)
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(ctx.get(m.group(1), "")), template)


def conditions_met(cond: dict, customer: Customer, order: Order | None) -> bool:
    if "min_total" in cond and (order is None or order.total < float(cond["min_total"])):
        return False
    if "segment" in cond and customer.segment != cond["segment"]:
        return False
    if "to_segment" in cond and cond["to_segment"] != customer.segment:
        return False
    return True


def queue_message(db: Session, customer: Customer, subject: str, body: str, *, origin: str,
                  workflow_id: int | None = None, dedupe_key: str | None = None,
                  sender: EmailSender | None = None, draft: bool = False) -> Communication | None:
    """Create a Communication (sent or draft). Returns None if dedupe_key already used."""
    status = "draft" if draft else ("sent" if (sender or RecordingSender()).send(customer.email, subject, body) else "failed")
    comm = Communication(customer_id=customer.id, workflow_id=workflow_id, subject=subject, body=body,
                         status=status, origin=origin, dedupe_key=dedupe_key)
    try:
        with db.begin_nested():
            db.add(comm)
            db.flush()
    except IntegrityError:
        return None
    return comm


class WorkflowEngine:
    def __init__(self, sender: EmailSender | None = None):
        self.sender = sender or RecordingSender()

    def handle(self, db: Session, event: Event) -> None:
        customer = db.get(Customer, event.customer_id)
        order = db.get(Order, event.order_id) if event.order_id else None
        flows = db.scalars(select(Workflow).where(Workflow.enabled.is_(True), Workflow.trigger == event.type)).all()
        for wf in flows:
            if not conditions_met(wf.conditions or {}, customer, order):
                continue
            if wf.action == "add_tag":
                if wf.tag and wf.tag not in customer.tags:
                    customer.tags = [*customer.tags, wf.tag]
            elif wf.action == "send_email":
                key = f"wf{wf.id}:c{customer.id}:o{order.id if order else 0}:{event.type}"
                queue_message(db, customer, render(wf.subject, customer, order, event.data),
                              render(wf.body, customer, order, event.data), origin="workflow",
                              workflow_id=wf.id, dedupe_key=key, sender=self.sender)


DEFAULT_WORKFLOWS = [
    dict(name="Welcome new customer", trigger="customer.created", action="send_email",
         subject="Welcome, {{first_name}}!", body="Hi {{first_name}}, thanks for joining us. Here's 10% off your first order: WELCOME10."),
    dict(name="Order confirmation", trigger="order.created", action="send_email",
         subject="We got your order #{{order_number}}", body="Hi {{first_name}}, order #{{order_number}} ({{currency}} {{order_total}}) is confirmed."),
    dict(name="Order completed — thank you", trigger="order.status:completed", action="send_email",
         subject="Your order #{{order_number}} is complete", body="Hi {{first_name}}, your order has been delivered. We'd love your feedback!"),
    dict(name="VIP big-spender thank you", trigger="order.created", conditions={"min_total": 3000}, action="send_email",
         subject="A special thank-you, {{first_name}}", body="Your order of {{currency}} {{order_total}} qualifies you for free priority shipping on your next order."),
    dict(name="Tag customers who became at-risk", trigger="segment.changed", conditions={"to_segment": "at_risk"},
         action="add_tag", tag="needs-winback"),
]


def seed_default_workflows(db: Session) -> None:
    if db.scalar(select(Workflow.id).limit(1)) is None:
        db.add_all(Workflow(**w) for w in DEFAULT_WORKFLOWS)
        db.commit()
