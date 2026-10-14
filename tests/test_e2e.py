"""End-to-end through the HTTP API: sync -> webhook -> workflow -> approval -> agent."""
import json

from app.integrations.woo import sign_payload

SECRET = "demo-webhook-secret"


def post_webhook(client, payload, topic, delivery, secret=SECRET):
    body = json.dumps(payload).encode()
    return client.post("/api/webhooks/woocommerce", content=body, headers={
        "X-WC-Webhook-Signature": sign_payload(secret, body), "X-WC-Webhook-ID": delivery,
        "X-WC-Webhook-Topic": topic, "Content-Type": "application/json"})


def test_seeded_import_is_quiet_and_segmented(client):
    s = client.get("/api/stats").json()
    assert s["customers"] == 40 and s["orders"] == 80
    assert s["emails_sent"] == 0  # historical import must not email anyone
    assert sum(s["segments"].values()) == 40 and s["segments"]["prospect"] > 0


def test_full_sync_is_idempotent(client):
    before = client.get("/api/stats").json()
    assert client.post("/api/sync").status_code == 200
    after = client.get("/api/stats").json()
    assert (before["customers"], before["orders"]) == (after["customers"], after["orders"])


def test_empty_store_first_sync_then_search(empty_client):
    assert empty_client.get("/api/customers").json() == []
    assert empty_client.post("/api/sync").json()["customers"] == 40
    rows = empty_client.get("/api/customers", params={"segment": "vip"}).json()
    assert all(r["segment"] == "vip" for r in rows)
    one = empty_client.get(f"/api/customers/{rows[0]['id']}").json() if rows else None
    assert one is None or one["orders"]


def test_webhook_rejects_bad_signature(client):
    r = post_webhook(client, {"id": 1}, "order.created", "d1", secret="wrong")
    assert r.status_code == 401


def test_new_order_webhook_triggers_workflow_and_dedupes(client):
    cust = client.get("/api/customers").json()[0]
    detail = client.get(f"/api/customers/{cust['id']}").json()
    payload = {"id": 99001, "number": "99001", "status": "processing", "total": "4200.00", "currency": "INR",
               "customer_id": 0, "date_created": "2026-10-14T09:00:00",
               "billing": {"email": cust["email"], "first_name": "X"}, "line_items": [{"name": "Saree", "quantity": 1, "total": "4200.00"}]}
    assert post_webhook(client, payload, "order.created", "dlv-1").json()["status"] == "processed"
    assert post_webhook(client, payload, "order.created", "dlv-1").json()["status"] == "duplicate"  # Woo retry
    comms = client.get(f"/api/customers/{cust['id']}").json()["communications"]
    subjects = [m["subject"] for m in comms]
    assert "We got your order #99001" in subjects
    assert any("special thank-you" in s for s in subjects)  # >= 3000 condition
    assert len(detail["orders"]) + 1 == len(client.get(f"/api/customers/{cust['id']}").json()["orders"])


def test_simulate_complete_order_sends_email_and_tracks_history(client):
    sim = client.post("/api/mock/simulate", json={"action": "new_order"}).json()
    assert sim["status"] == "processed"
    done = client.post("/api/mock/simulate", json={"action": "complete_order"}).json()
    assert done["status"] == "processed"
    comms = client.get("/api/communications").json()
    assert any("is complete" in m["subject"] for m in comms)
    completed = [o for o in client.get("/api/orders", params={"status": "completed"}).json() if len(o["history"]) >= 2]
    assert completed


def test_agent_retention_run_then_human_approval(client):
    drafts = client.post("/api/agent/retention-run").json()["drafts"]
    assert drafts, "seed data should contain at-risk/churned customers"
    pending = client.get("/api/communications", params={"status": "draft"}).json()
    assert len(pending) == len(drafts)
    sent = client.post(f"/api/communications/{pending[0]['id']}/approve").json()
    assert sent["status"] == "sent"
    assert client.post(f"/api/communications/{pending[0]['id']}/approve").status_code == 404  # no double send


def test_agent_chat_offline_fallback(client):
    r = client.post("/api/agent/chat", json={"message": "give me an overview"}).json()
    assert r["engine"] == "rules" and "Segment overview" in r["reply"]


def test_workflow_crud_and_notes(client):
    w = client.post("/api/workflows", json={"name": "T", "trigger": "customer.created", "subject": "s", "body": "b"})
    assert w.status_code == 201
    assert client.patch(f"/api/workflows/{w.json()['id']}", params={"enabled": False}).json()["enabled"] is False
    cid = client.get("/api/customers").json()[0]["id"]
    p = client.patch(f"/api/customers/{cid}", json={"tags": ["b2b", "b2b", " vip-friend "], "notes": "call Monday"}).json()
    assert p["tags"] == ["b2b", "vip-friend"] and p["notes"] == "call Monday"
    assert client.get("/api/customers/99999").status_code == 404
