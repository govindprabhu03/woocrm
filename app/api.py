import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.agent.agent import build_agent
from app.agent.tools import retention_run
from app.auth import require_auth
from app.db import utcnow
from app.domain.segmentation import SEGMENTS, rfm_score
from app.integrations.woo import sign_payload, verify_signature
from app.models import Communication, Customer, Order, WebhookEvent, Workflow
from app.services import sync
from app.services.workflows import queue_message

MAX_WEBHOOK_BYTES = 1_000_000
router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])  # admin-only
public = APIRouter(prefix="/api")  # webhook: authenticated by HMAC signature instead


def get_db(request: Request):
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def customer_dict(c: Customer, detail: bool = False) -> dict:
    d = {"id": c.id, "name": c.name, "email": c.email, "city": c.city, "segment": c.segment, "tags": c.tags,
         "order_count": c.order_count, "total_spent": c.total_spent, "rfm": rfm_score(c.order_count, c.total_spent, c.last_order_at, utcnow()),
         "last_order_at": c.last_order_at.isoformat() if c.last_order_at else None}
    if detail:
        d.update(phone=c.phone, notes=c.notes, created_at=c.created_at.isoformat(),
                 orders=[order_dict(o) for o in c.orders], communications=[comm_dict(m) for m in c.communications])
    return d


def order_dict(o: Order) -> dict:
    return {"id": o.id, "number": o.number, "status": o.status, "total": o.total, "currency": o.currency,
            "items": o.items, "created_at": o.created_at.isoformat(), "history": o.history,
            "customer_id": o.customer_id, "customer": o.customer.name if o.customer else None}


def comm_dict(m: Communication) -> dict:
    return {"id": m.id, "customer_id": m.customer_id, "customer": m.customer.name if m.customer else None,
            "subject": m.subject, "body": m.body, "status": m.status, "origin": m.origin,
            "workflow_id": m.workflow_id, "created_at": m.created_at.isoformat()}


def wf_dict(w: Workflow) -> dict:
    return {c: getattr(w, c) for c in ("id", "name", "trigger", "conditions", "action", "subject", "body", "tag", "enabled")}


# ---------- dashboard / customers / orders ----------
@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    seg = dict(db.execute(select(Customer.segment, func.count()).group_by(Customer.segment)).all())
    status = dict(db.execute(select(Order.status, func.count()).group_by(Order.status)).all())
    return {"customers": sum(seg.values()), "orders": sum(status.values()),
            "revenue": db.scalar(select(func.coalesce(func.sum(Customer.total_spent), 0))),
            "segments": {s: seg.get(s, 0) for s in SEGMENTS}, "order_status": status,
            "drafts": db.scalar(select(func.count()).select_from(Communication).where(Communication.status == "draft")),
            "emails_sent": db.scalar(select(func.count()).select_from(Communication).where(Communication.status == "sent"))}


@router.get("/customers")
def list_customers(segment: str | None = None, q: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Customer).order_by(Customer.total_spent.desc())
    if segment:
        stmt = stmt.where(Customer.segment == segment)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(Customer.email).like(like) | func.lower(Customer.first_name).like(like)
                          | func.lower(Customer.last_name).like(like))
    return [customer_dict(c) for c in db.scalars(stmt)]


@router.get("/customers/{cid}")
def get_customer(cid: int, db: Session = Depends(get_db)):
    c = db.get(Customer, cid)
    if not c:
        raise HTTPException(404, "Customer not found")
    return customer_dict(c, detail=True)


class CustomerPatch(BaseModel):
    tags: list[Annotated[str, Field(max_length=50)]] | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=5000)


@router.patch("/customers/{cid}")
def patch_customer(cid: int, body: CustomerPatch, db: Session = Depends(get_db)):
    c = db.get(Customer, cid)
    if not c:
        raise HTTPException(404, "Customer not found")
    if body.tags is not None:
        c.tags = sorted(set(t.strip() for t in body.tags if t.strip()))
    if body.notes is not None:
        c.notes = body.notes
    db.commit()
    return customer_dict(c, detail=True)


@router.get("/orders")
def list_orders(status: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Order).options(joinedload(Order.customer)).order_by(Order.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(Order.status == status)
    return [order_dict(o) for o in db.scalars(stmt)]


# ---------- sync & webhooks ----------
@router.post("/sync")
def run_sync(request: Request, db: Session = Depends(get_db)):
    s = request.app.state
    return sync.full_sync(db, s.bus, s.woo, quiet=db.scalar(select(func.count()).select_from(Customer)) == 0)


def ingest_webhook(db: Session, bus, delivery_id: str, topic: str, payload: dict) -> dict:
    if db.scalar(select(WebhookEvent.id).where(WebhookEvent.delivery_id == delivery_id)):
        return {"status": "duplicate"}
    db.add(WebhookEvent(delivery_id=delivery_id, topic=topic))
    if topic.startswith("order."):
        sync.upsert_order(db, bus, payload)
    elif topic.startswith("customer."):
        from app.integrations.woo import map_customer

        sync.upsert_customer(db, bus, map_customer(payload))
    else:
        return {"status": "ignored"}
    db.commit()
    return {"status": "processed", "topic": topic}


@public.post("/webhooks/woocommerce")
async def woo_webhook(request: Request, db: Session = Depends(get_db),
                      x_wc_webhook_signature: str = Header(""), x_wc_webhook_id: str = Header(""),
                      x_wc_webhook_topic: str = Header("")):
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "Payload too large")
    if not verify_signature(request.app.state.settings.webhook_secret, body, x_wc_webhook_signature):
        raise HTTPException(401, "Invalid webhook signature")
    if not x_wc_webhook_id or not x_wc_webhook_topic:
        raise HTTPException(400, "Missing webhook headers")
    try:
        return ingest_webhook(db, request.app.state.bus, x_wc_webhook_id, x_wc_webhook_topic, json.loads(body or b"{}"))
    except (ValueError, KeyError, TypeError, AttributeError):
        db.rollback()  # delivery stays unrecorded so a corrected retry is processed
        raise HTTPException(422, "Malformed webhook payload")


class Simulate(BaseModel):
    action: Literal["new_order", "complete_order", "new_customer"]
    customer_id: int | None = None
    order_id: int | None = None


@router.post("/mock/simulate")
def simulate(body: Simulate, request: Request, db: Session = Depends(get_db)):
    """Demo helper: mutate the mock store and deliver a *signed* webhook through the real ingestion path."""
    st = request.app.state
    if not (st.settings.use_mock_store and st.settings.allow_simulation):
        raise HTTPException(400, "Simulation only available with the built-in mock store")
    store = st.woo
    if body.action == "new_customer":
        payload, topic = store.add_customer("Demo", f"User{len(list(store.customers())) + 1}"), "customer.created"
    elif body.action == "new_order":
        c = db.get(Customer, body.customer_id) if body.customer_id else db.scalars(select(Customer).where(Customer.woo_id.is_not(None)).order_by(func.random())).first()
        payload, topic = store.add_order(c.woo_id, status="processing", created=utcnow()), "order.created"
    else:
        o = db.get(Order, body.order_id) if body.order_id else db.scalars(select(Order).where(Order.status == "processing")).first()
        if not o:
            raise HTTPException(404, "No order to complete")
        payload, topic = store.set_status(o.woo_id, "completed"), "order.updated"
    raw = json.dumps(payload).encode()
    st.delivery += 1
    result = ingest_webhook(db, st.bus, f"sim-{st.delivery}", topic, json.loads(raw))
    return {**result, "signature": sign_payload(st.settings.webhook_secret, raw), "payload_id": payload["id"]}


# ---------- workflows & communications ----------
class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    trigger: str = Field(pattern=r"^(customer\.created|order\.created|order\.status:[a-z-]{3,20}|segment\.changed)$")
    conditions: dict = {}
    action: Literal["send_email", "add_tag"] = "send_email"
    subject: str = Field(default="", max_length=255)
    body: str = Field(default="", max_length=5000)
    tag: str = Field(default="", max_length=50)
    enabled: bool = True


@router.get("/workflows")
def list_workflows(db: Session = Depends(get_db)):
    return [wf_dict(w) for w in db.scalars(select(Workflow).order_by(Workflow.id))]


@router.post("/workflows", status_code=201)
def create_workflow(body: WorkflowIn, db: Session = Depends(get_db)):
    w = Workflow(**body.model_dump())
    db.add(w)
    db.commit()
    return wf_dict(w)


@router.patch("/workflows/{wid}")
def toggle_workflow(wid: int, enabled: bool, db: Session = Depends(get_db)):
    w = db.get(Workflow, wid)
    if not w:
        raise HTTPException(404, "Workflow not found")
    w.enabled = enabled
    db.commit()
    return wf_dict(w)


@router.get("/communications")
def list_comms(status: str | None = None, db: Session = Depends(get_db)):
    stmt = (select(Communication).options(joinedload(Communication.customer))
            .order_by(Communication.created_at.desc(), Communication.id.desc()).limit(200))
    if status:
        stmt = stmt.where(Communication.status == status)
    return [comm_dict(m) for m in db.scalars(stmt)]


@router.post("/communications/{mid}/approve")
def approve(mid: int, request: Request, db: Session = Depends(get_db)):
    m = db.get(Communication, mid)
    if not m or m.status != "draft":
        raise HTTPException(404, "Draft not found")
    ok = request.app.state.sender.send(m.customer.email, m.subject, m.body)
    m.status = "sent" if ok else "failed"
    db.commit()
    return comm_dict(m)


# ---------- agent ----------
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@router.post("/agent/chat")
def agent_chat(body: ChatIn, request: Request, db: Session = Depends(get_db)):
    s = request.app.state.settings
    return build_agent(db, s.anthropic_key, s.llm_model).chat(body.message)


@router.post("/agent/retention-run")
def agent_retention(db: Session = Depends(get_db)):
    return {"drafts": retention_run(db)}
