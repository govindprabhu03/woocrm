"""CRM tools exposed to the agent (same functions back both the Gemini function-calling loop and the offline fallback)."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Communication, Customer, Workflow
from app.services.workflows import queue_message

TOOL_SPECS = [
    {"name": "segment_summary", "description": "Customer counts and revenue per segment.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "find_customers", "description": "Search customers by segment and/or name/email text.",
     "input_schema": {"type": "object", "properties": {"segment": {"type": "string",
                      "enum": ["vip", "new", "active", "at_risk", "churned", "prospect"]},
                      "query": {"type": "string"}, "limit": {"type": "integer"}}}},
    {"name": "get_customer", "description": "Full detail for one customer incl. recent orders.",
     "input_schema": {"type": "object", "properties": {"customer_id": {"type": "integer"}}, "required": ["customer_id"]}},
    {"name": "draft_email", "description": "Save a DRAFT email to a customer for human approval (never sends).",
     "input_schema": {"type": "object", "properties": {"customer_id": {"type": "integer"}, "subject": {"type": "string"},
                      "body": {"type": "string"}}, "required": ["customer_id", "subject", "body"]}},
    {"name": "create_workflow", "description": "Create an automation workflow.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "trigger": {"type": "string",
                      "description": "customer.created | order.created | order.status:<status> | segment.changed"},
                      "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["name", "trigger", "subject", "body"]}},
]


def _cust_row(c: Customer) -> dict:
    return {"id": c.id, "name": c.name, "email": c.email, "segment": c.segment, "orders": c.order_count,
            "spent": c.total_spent, "last_order": c.last_order_at.date().isoformat() if c.last_order_at else None}


class CrmTools:
    def __init__(self, db: Session):
        self.db = db

    def call(self, name: str, args: dict):
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(**args)
        except TypeError as e:
            return {"error": str(e)}

    def t_segment_summary(self):
        rows = self.db.execute(select(Customer.segment, func.count(), func.coalesce(func.sum(Customer.total_spent), 0))
                               .group_by(Customer.segment)).all()
        return {s: {"customers": n, "revenue": round(r, 2)} for s, n, r in rows}

    def t_find_customers(self, segment: str | None = None, query: str | None = None, limit: int = 10):
        stmt = select(Customer).order_by(Customer.total_spent.desc()).limit(min(limit, 25))
        if segment:
            stmt = stmt.where(Customer.segment == segment)
        if query:
            like = f"%{query.lower()}%"
            stmt = stmt.where(func.lower(Customer.email).like(like) | func.lower(Customer.first_name).like(like)
                              | func.lower(Customer.last_name).like(like))
        return [_cust_row(c) for c in self.db.scalars(stmt)]

    def t_get_customer(self, customer_id: int):
        c = self.db.get(Customer, customer_id)
        if not c:
            return {"error": "not found"}
        return {**_cust_row(c), "city": c.city, "tags": c.tags,
                "recent_orders": [{"number": o.number, "status": o.status, "total": o.total,
                                   "items": [i["name"] for i in o.items]} for o in c.orders[:5]]}

    def t_draft_email(self, customer_id: int, subject: str, body: str):
        c = self.db.get(Customer, customer_id)
        if not c:
            return {"error": "not found"}
        comm = queue_message(self.db, c, subject, body, origin="agent", draft=True)
        self.db.commit()
        return {"draft_id": comm.id, "to": c.email, "status": "draft — awaiting human approval"}

    def t_create_workflow(self, name: str, trigger: str, subject: str, body: str):
        wf = Workflow(name=name, trigger=trigger, action="send_email", subject=subject, body=body)
        self.db.add(wf)
        self.db.commit()
        return {"workflow_id": wf.id, "name": name}


def winback_draft(c: Customer) -> tuple[str, str]:
    fav = c.orders[0].items[0]["name"] if c.orders and c.orders[0].items else "our products"
    return (f"We miss you, {c.first_name or 'friend'}!",
            f"Hi {c.first_name or 'there'}, it's been a while since your last order. "
            f"Your favourites like {fav} are waiting — use COMEBACK15 for 15% off.")


def retention_run(db: Session, limit: int = 10, writer=None) -> list[dict]:
    """Autonomous agent task: find at-risk/churned high-value customers and draft win-back emails (human approves).
    `writer(customer) -> (subject, body)` may be an LLM; defaults to a template."""
    writer = writer or winback_draft
    targets = db.scalars(select(Customer).where(Customer.segment.in_(["at_risk", "churned"]))
                         .order_by(Customer.total_spent.desc()).limit(limit)).all()
    out = []
    for c in targets:
        already = db.scalar(select(Communication.id).where(Communication.customer_id == c.id,
                            Communication.origin == "agent", Communication.status == "draft"))
        if already:
            continue
        subject, body = writer(c)
        comm = queue_message(db, c, subject, body, origin="agent", draft=True)
        out.append({"customer": c.name, "segment": c.segment, "spent": c.total_spent, "draft_id": comm.id})
    db.commit()
    return out
