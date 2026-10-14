"""Security & robustness tests: authentication, session integrity, brute force, CSRF, headers, input hardening."""
import json
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.auth import LoginLimiter, make_token, read_token
from app.integrations.woo import sign_payload
from app.main import create_app

PW = "correct-horse-battery"
SECRET = "s" * 40
WH = "webhook-secret-0123456789"


@pytest.fixture
def secured(settings):
    return replace(settings, admin_password=PW, session_secret=SECRET, webhook_secret=WH)


@pytest.fixture
def ac(secured):
    return TestClient(create_app(secured))


def login(c, pw=PW, user="admin"):
    return c.post("/api/auth/login", json={"username": user, "password": pw})


# ---- authentication ----
def test_api_requires_login_but_health_and_static_do_not(ac):
    assert ac.get("/api/customers").status_code == 401
    assert ac.get("/api/stats").status_code == 401
    assert ac.post("/api/sync").status_code == 401
    assert ac.post("/api/agent/retention-run").status_code == 401
    assert ac.get("/api/agent/status").status_code == 401
    assert ac.get("/health").status_code == 200 and ac.get("/").status_code == 200


def test_login_flow_and_logout(ac):
    me = ac.get("/api/auth/me").json()
    assert (me["auth_required"], me["authenticated"], me["user"], me["google_enabled"]) == (True, False, None, False)
    assert login(ac, "wrong").status_code == 401
    assert login(ac, user="root").status_code == 401
    r = login(ac)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert ac.get("/api/customers").status_code == 200
    assert ac.get("/api/auth/me").json()["authenticated"] is True
    ac.post("/api/auth/logout")
    assert ac.get("/api/customers").status_code == 401


def test_tampered_forged_and_expired_tokens_rejected(ac):
    good = make_token(SECRET, "admin")
    payload, sig = good.split(".")
    for bad in (payload + ".AAAA", "x." + sig, "garbage", "", make_token("other-secret-" * 4, "admin")):
        ac.cookies.set("woocrm_session", bad)
        assert ac.get("/api/customers").status_code == 401, bad
    assert read_token(SECRET, good) == "admin"
    assert read_token(SECRET, good, now=time.time() + 9 * 3600) is None  # expired after 8h


def test_brute_force_lockout(ac):
    for _ in range(5):
        assert login(ac, "bad").status_code == 401
    assert login(ac, PW).status_code == 429  # even the right password is refused while locked


def test_limiter_window_expires():
    lim = LoginLimiter(max_fail=2, window=1)
    lim.fail("ip"), lim.fail("ip")
    assert lim.blocked("ip")
    time.sleep(1.1)
    assert not lim.blocked("ip")


def test_webhook_uses_hmac_not_session(ac):
    body = json.dumps({"id": 777, "number": "777", "status": "processing", "total": "10", "customer_id": 0,
                       "billing": {"email": "hook@x.com"}, "line_items": []}).encode()
    h = {"X-WC-Webhook-ID": "w1", "X-WC-Webhook-Topic": "order.created", "Content-Type": "application/json"}
    assert ac.post("/api/webhooks/woocommerce", content=body, headers={**h, "X-WC-Webhook-Signature": "bad"}).status_code == 401
    ok = ac.post("/api/webhooks/woocommerce", content=body, headers={**h, "X-WC-Webhook-Signature": sign_payload(WH, body)})
    assert ok.json()["status"] == "processed"  # no admin session needed


# ---- production configuration (fail closed) ----
def test_production_refuses_weak_config(settings):
    with pytest.raises(RuntimeError) as e:
        create_app(replace(settings, env="production"))
    msg = str(e.value)
    assert "ADMIN_PASSWORD" in msg and "SESSION_SECRET" in msg and "WOO_WEBHOOK_SECRET" in msg


def test_production_ok_with_strong_config_and_hardened(secured):
    prod = replace(secured, env="production", allow_simulation=False)
    c = TestClient(create_app(prod), base_url="https://testserver")  # Secure cookie => HTTPS only
    r = c.get("/health")
    assert "max-age" in r.headers["strict-transport-security"]
    login(c)
    assert c.post("/api/mock/simulate", json={"action": "new_order"}).status_code == 400  # demo helper off in prod


# ---- headers & CSRF ----
def test_security_headers(client):
    h = client.get("/").headers
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in h["content-security-policy"] and h["referrer-policy"] == "no-referrer"
    assert client.get("/api/stats").headers["cache-control"] == "no-store"


def test_cross_origin_post_blocked_same_origin_allowed(ac):
    login(ac)
    evil = ac.post("/api/sync", headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    assert ac.post("/api/sync", headers={"Origin": "http://testserver"}).status_code == 200


# ---- input hardening / robustness ----
def sig_post(c, body: bytes, delivery="d-x", topic="order.created", secret="demo-webhook-secret"):
    return c.post("/api/webhooks/woocommerce", content=body, headers={
        "X-WC-Webhook-Signature": sign_payload(secret, body), "X-WC-Webhook-ID": delivery, "X-WC-Webhook-Topic": topic})


def test_webhook_oversized_payload_rejected(client):
    assert sig_post(client, b"{" + b" " * 1_100_000 + b"}").status_code == 413


def test_webhook_malformed_payloads_are_422_not_500_and_retry_still_works(client):
    assert sig_post(client, b"not json", "bad1").status_code == 422
    assert sig_post(client, b'{"no_id": true}', "bad2").status_code == 422  # missing order id
    assert sig_post(client, b"[]", "bad3").status_code == 422  # wrong JSON shape
    good = {"id": 4242, "number": "4242", "status": "processing", "total": "5", "customer_id": 0,
            "billing": {"email": "retry@x.com"}, "line_items": []}
    assert sig_post(client, json.dumps(good).encode(), "bad2").json()["status"] == "processed"  # same delivery id, fixed payload


def test_webhook_missing_headers(client):
    body = b"{}"
    r = client.post("/api/webhooks/woocommerce", content=body, headers={"X-WC-Webhook-Signature": sign_payload("demo-webhook-secret", body)})
    assert r.status_code == 400


def test_validation_limits(client):
    cid = client.get("/api/customers").json()[0]["id"]
    assert client.patch(f"/api/customers/{cid}", json={"notes": "x" * 5001}).status_code == 422
    assert client.patch(f"/api/customers/{cid}", json={"tags": ["t"] * 21}).status_code == 422
    assert client.post("/api/workflows", json={"name": "x", "trigger": "evil; DROP TABLE"}).status_code == 422
    assert client.post("/api/workflows", json={"name": "", "trigger": "order.created"}).status_code == 422
    assert client.post("/api/agent/chat", json={"message": "x" * 2001}).status_code == 422
    assert client.post("/api/agent/chat", json={"message": ""}).status_code == 422


def test_sql_injection_attempts_are_inert(client):
    for evil in ["' OR '1'='1", "'; DROP TABLE customers;--", "%", "_"]:
        assert client.get("/api/customers", params={"q": evil}).status_code == 200
        assert client.get("/api/customers", params={"segment": evil}).json() == []
    assert client.get("/api/stats").json()["customers"] == 40  # nothing dropped


def test_stored_xss_payload_is_returned_as_inert_json(client):
    cid = client.get("/api/customers").json()[0]["id"]
    xss = '<img src=x onerror=alert(1)>'
    r = client.patch(f"/api/customers/{cid}", json={"notes": xss, "tags": [xss]})
    assert r.headers["content-type"].startswith("application/json") and r.json()["notes"] == xss


def test_guest_order_merges_by_email_case_insensitive(client):
    base = client.get("/api/stats").json()["customers"]
    for i, mail in enumerate(["New.Person@Example.com", "new.person@example.COM"]):
        body = json.dumps({"id": 9000 + i, "number": str(9000 + i), "status": "processing", "total": "100", "customer_id": 0,
                           "billing": {"email": mail, "first_name": "New"}, "line_items": []}).encode()
        assert sig_post(client, body, f"g{i}").json()["status"] == "processed"
    assert client.get("/api/stats").json()["customers"] == base + 1


def test_unknown_routes_and_methods(client):
    assert client.get("/api/nope").status_code == 404
    assert client.delete("/api/customers/1").status_code == 405
    assert client.get("/api/orders", params={"status": "'; --"}).json() == []
