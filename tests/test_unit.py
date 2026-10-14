from datetime import datetime, timedelta

from app.agent.agent import ClaudeAgent, RuleBasedAgent
from app.agent.tools import CrmTools, retention_run
from app.db import make_session_factory
from app.domain.segmentation import compute_segment, rfm_score
from app.integrations.woo import map_customer, map_order, sign_payload, verify_signature
from app.models import Communication, Customer
from app.services.events import EventBus
from app.services.sync import upsert_order
from app.services.workflows import WorkflowEngine, render, seed_default_workflows

NOW = datetime(2026, 10, 14)


# ---- segmentation ----
def test_segments():
    d = lambda n: NOW - timedelta(days=n)
    assert compute_segment(0, 0, None, NOW) == "prospect"
    assert compute_segment(1, 500, d(5), NOW) == "new"
    assert compute_segment(2, 900, d(20), NOW) == "active"
    assert compute_segment(6, 900, d(20), NOW) == "vip"
    assert compute_segment(2, 9000, d(20), NOW) == "vip"
    assert compute_segment(3, 900, d(90), NOW) == "at_risk"
    assert compute_segment(9, 99999, d(200), NOW) == "churned"  # recency beats value


def test_rfm_bounds():
    assert rfm_score(0, 0, None, NOW) == 0
    assert rfm_score(5, 9000, NOW, NOW) == 15


# ---- woo adapter ----
def test_signature_roundtrip():
    sig = sign_payload("s3cret", b'{"id":1}')
    assert verify_signature("s3cret", b'{"id":1}', sig)
    assert not verify_signature("s3cret", b'{"id":2}', sig)
    assert not verify_signature("wrong", b'{"id":1}', sig)
    assert not verify_signature("s3cret", b"{}", "")


def test_map_order_guest_checkout():
    raw = {"id": 5, "number": "5", "status": "processing", "total": "1299.00", "customer_id": 0,
           "date_created": "2026-10-01T10:00:00", "billing": {"email": "Guest@X.com", "first_name": "G"},
           "line_items": [{"name": "Box", "quantity": 2, "total": "1299.00"}]}
    m = map_order(raw)
    assert m["woo_customer_id"] is None and m["total"] == 1299.0
    assert m["billing"]["email"] == "guest@x.com" and m["items"][0]["quantity"] == 2


def test_map_customer_falls_back_to_billing():
    assert map_customer({"billing": {"email": "A@b.com", "first_name": "A"}})["email"] == "a@b.com"


# ---- workflow engine ----
def _db():
    sf = make_session_factory("sqlite://")
    db = sf()
    seed_default_workflows(db)
    bus = EventBus()
    bus.subscribe(WorkflowEngine().handle)
    return db, bus


def _order(i, total="500.00", status="processing", email="a@x.com"):
    return {"id": i, "number": str(i), "status": status, "total": total, "customer_id": 0,
            "date_created": datetime.utcnow().isoformat(), "billing": {"email": email, "first_name": "Asha"}, "line_items": []}


def test_render_template():
    c = Customer(email="a@x.com", first_name="Asha", segment="vip", total_spent=10)
    assert render("Hi {{first_name}} {{ missing }}!", c) == "Hi Asha !"


def test_new_order_triggers_welcome_and_confirmation_once():
    db, bus = _db()
    upsert_order(db, bus, _order(1))
    upsert_order(db, bus, _order(1))  # replay must not duplicate
    db.commit()
    subjects = sorted(m.subject for m in db.query(Communication))
    assert subjects == ["We got your order #1", "Welcome, Asha!"]


def test_condition_min_total_vip_workflow():
    db, bus = _db()
    upsert_order(db, bus, _order(1, total="3500.00"))
    assert any("special thank-you" in m.subject for m in db.query(Communication))
    upsert_order(db, bus, _order(2, total="100.00", email="b@x.com"))
    assert not any(m.customer.email == "b@x.com" and "thank-you" in m.subject for m in db.query(Communication))


def test_status_change_fires_completed_workflow():
    db, bus = _db()
    upsert_order(db, bus, _order(1))
    upsert_order(db, bus, _order(1, status="completed"))
    assert any("is complete" in m.subject for m in db.query(Communication))
    order = db.query(Customer).one().orders[0]
    assert [h["status"] for h in order.history] == ["processing", "completed"]


def test_cancelled_orders_do_not_count_toward_ltv():
    db, bus = _db()
    upsert_order(db, bus, _order(1, status="cancelled", total="999.00"))
    c = db.query(Customer).one()
    assert c.total_spent == 0 and c.segment == "prospect"


# ---- agent ----
def test_tools_draft_never_sends():
    db, bus = _db()
    upsert_order(db, bus, _order(1))
    cid = db.query(Customer).one().id
    out = CrmTools(db).call("draft_email", {"customer_id": cid, "subject": "Hi", "body": "Yo"})
    assert "awaiting" in out["status"]
    assert db.query(Communication).filter_by(origin="agent").one().status == "draft"
    assert CrmTools(db).call("nope", {})["error"]


def test_retention_run_targets_at_risk_and_is_idempotent():
    db, _ = _db()
    c = Customer(email="old@x.com", first_name="Old", segment="at_risk", total_spent=5000, order_count=3)
    db.add(c)
    db.commit()
    assert len(retention_run(db)) == 1
    assert retention_run(db) == []


def test_rule_agent_routes_to_tools():
    db, _ = _db()
    db.add(Customer(email="v@x.com", first_name="Vee", segment="vip", total_spent=9000, order_count=6))
    db.commit()
    r = RuleBasedAgent(db).chat("show me my vip customers")
    assert r["engine"] == "rules" and "Vee" in r["reply"] and r["tool_calls"][0]["tool"] == "find_customers"


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeLLM:
    """Scripted Claude: first asks for a tool, then answers — verifies the tool-use loop wiring."""

    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        if self.calls == 1:
            blk = _Block(type="tool_use", id="t1", name="segment_summary", input={})
            return _Block(stop_reason="tool_use", content=[blk])
        return _Block(stop_reason="end_turn", content=[_Block(type="text", text="You have 1 vip.")])


def test_claude_agent_tool_loop():
    db, _ = _db()
    db.add(Customer(email="v@x.com", segment="vip", total_spent=1))
    db.commit()
    r = ClaudeAgent(db, "k", "m", client=_FakeLLM()).chat("summary?")
    assert r["reply"] == "You have 1 vip." and r["tool_calls"][0]["tool"] == "segment_summary"
