from datetime import datetime, timedelta

import json

import httpx

from app.agent.agent import GeminiAgent, RuleBasedAgent, gemini_declarations
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


def _gemini(handler, db):
    return GeminiAgent(db, "test-key", "gemini-test", http=httpx.Client(transport=httpx.MockTransport(handler)))


def _seeded_db():
    db, _ = _db()
    db.add(Customer(email="v@x.com", first_name="Vee", segment="vip", total_spent=9000, order_count=6))
    db.commit()
    return db


def test_gemini_tool_loop_executes_tools_and_echoes_model_parts():
    db, requests = _seeded_db(), []
    call_part = {"functionCall": {"name": "find_customers", "args": {"segment": "vip"}}, "thoughtSignature": "sig123"}

    def handler(req):
        body = json.loads(req.content)
        requests.append((req.headers["x-goog-api-key"], body))
        if len(requests) == 1:
            return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [call_part]}}]})
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": "Vee is your VIP."}]}}]})

    r = _gemini(handler, db).chat("who are my vips?")
    assert r == {"reply": "Vee is your VIP.", "tool_calls": [{"tool": "find_customers", "args": {"segment": "vip"}}], "engine": "gemini"}
    key, first = requests[0]
    assert key == "test-key" and "key=" not in str(first)  # key travels in a header, not the URL/body
    second = requests[1][1]["contents"]
    assert second[1]["parts"][0]["thoughtSignature"] == "sig123"  # model turn echoed verbatim
    tool_result = second[2]["parts"][0]["functionResponse"]
    assert tool_result["name"] == "find_customers" and tool_result["response"]["result"][0]["name"] == "Vee"


def test_gemini_declarations_are_valid_shape():
    decls = {d["name"]: d for d in gemini_declarations()}
    assert "parameters" not in decls["segment_summary"]  # Gemini rejects empty parameter objects
    p = decls["draft_email"]["parameters"]
    assert p["type"] == "OBJECT" and p["properties"]["customer_id"]["type"] == "INTEGER" and "customer_id" in p["required"]
    assert decls["find_customers"]["parameters"]["properties"]["segment"]["enum"]


def test_gemini_failures_fall_back_to_rules_and_say_why():
    db = _seeded_db()
    quota = _gemini(lambda r: httpx.Response(429, json={"error": {"message": "quota"}}), db).chat("show vip customers")
    assert quota["engine"] == "rules (gemini unavailable: HTTP 429)" and "Vee" in quota["reply"]
    bad = _gemini(lambda r: httpx.Response(200, json={"candidates": []}), db).chat("overview")
    assert bad["engine"].startswith("rules (gemini unavailable") and "Segment overview" in bad["reply"]

    def down(req):
        raise httpx.ConnectError("offline")

    assert _gemini(down, db).chat("overview")["engine"] == "rules (gemini unavailable: ConnectError)"


def test_gemini_stops_after_max_steps():
    db = _seeded_db()
    loop = {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "segment_summary", "args": {}}}]}}]}
    agent = _gemini(lambda r: httpx.Response(200, json=loop), db)
    agent.max_steps = 3
    out = agent.chat("x")
    assert out["reply"] == "Stopped after max steps." and len(out["tool_calls"]) == 3
