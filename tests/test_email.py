"""SendGrid sender: request shape, failure handling, safety rules, and wiring into workflows."""
import json
from dataclasses import replace

import httpx

from app.models import Customer
from app.services.email import SendGridSender, build_sender
from app.services.workflows import RecordingSender, queue_message
from app.db import make_session_factory


def sender(handler, **kw):
    return SendGridSender("SG.key", "hello@shop.com", http=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_success_builds_correct_sendgrid_request():
    seen = {}

    def h(req):
        seen.update(url=str(req.url), auth=req.headers["authorization"], body=json.loads(req.content))
        return httpx.Response(202)

    assert sender(h).send("a@x.com", "Hi\nBcc: evil@x.com", "Hello") is True
    assert seen["url"] == "https://api.sendgrid.com/v3/mail/send" and seen["auth"] == "Bearer SG.key"
    b = seen["body"]
    assert b["personalizations"][0]["to"][0]["email"] == "a@x.com" and b["from"]["email"] == "hello@shop.com"
    assert "\n" not in b["subject"]  # header-injection attempt flattened
    assert b["content"][0]["value"].startswith("Hello") and "unsubscribe" in b["content"][0]["value"].lower()
    assert "mail_settings" not in b


def test_sandbox_mode_flag():
    seen = {}
    sender(lambda r: (seen.update(b=json.loads(r.content)), httpx.Response(202))[1], sandbox=True).send("a@x.com", "s", "b")
    assert seen["b"]["mail_settings"]["sandbox_mode"]["enable"] is True


def test_rejection_and_network_errors_return_false_not_raise():
    assert sender(lambda r: httpx.Response(401, json={"errors": [{"message": "bad key"}]})).send("a@x.com", "s", "b") is False
    assert sender(lambda r: httpx.Response(400)).send("a@x.com", "s", "b") is False

    def boom(req):
        raise httpx.ConnectError("down")

    assert sender(boom).send("a@x.com", "s", "b") is False


def test_invalid_recipient_never_calls_api():
    called = []
    s = sender(lambda r: (called.append(1), httpx.Response(202))[1])
    assert not s.send("not-an-email", "s", "b") and not s.send("a@x.com\nbcc:z@x.com", "s", "b") and not called


def test_build_sender_selects_provider(settings):
    assert isinstance(build_sender(settings), RecordingSender)
    real = build_sender(replace(settings, sendgrid_api_key="SG.k", mail_from="a@b.com", sendgrid_sandbox=True))
    assert isinstance(real, SendGridSender)
    assert isinstance(build_sender(replace(settings, sendgrid_api_key="SG.k")), RecordingSender)  # needs MAIL_FROM too


def test_failed_delivery_is_recorded_as_failed():
    db = make_session_factory("sqlite://")()
    c = Customer(email="a@x.com", first_name="A")
    db.add(c); db.commit()
    bad = sender(lambda r: httpx.Response(500))
    assert queue_message(db, c, "s", "b", origin="workflow", sender=bad).status == "failed"
    assert queue_message(db, c, "s", "b", origin="workflow", sender=sender(lambda r: httpx.Response(202))).status == "sent"


def test_unsubscribed_customers_are_never_emailed():
    db = make_session_factory("sqlite://")()
    c = Customer(email="a@x.com", tags=["unsubscribed"])
    db.add(c); db.commit()
    calls = []

    class Spy:
        def send(self, *a):
            calls.append(a)
            return True

    assert queue_message(db, c, "s", "b", origin="workflow", sender=Spy()).status == "skipped" and not calls


def test_approve_endpoint_uses_injected_sender_and_respects_unsubscribe(settings):
    from fastapi.testclient import TestClient
    from app.main import create_app

    sent = []

    class Spy:
        def send(self, to, subject, body):
            sent.append(to)
            return True

    c = TestClient(create_app(settings, sender=Spy()))
    drafts = c.post("/api/agent/retention-run").json()["drafts"]
    ids = [d["draft_id"] for d in drafts]
    assert c.post(f"/api/communications/{ids[0]}/approve").json()["status"] == "sent" and len(sent) == 1
    cust = next(m for m in c.get("/api/communications").json() if m["id"] == ids[1])["customer_id"]
    c.patch(f"/api/customers/{cust}", json={"tags": ["unsubscribed"]})
    assert c.post(f"/api/communications/{ids[1]}/approve").json()["status"] == "skipped" and len(sent) == 1
