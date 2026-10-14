"""Google sign-in: OAuth code flow + PKCE, allowlist enforcement, CSRF state, and failure handling (Google mocked)."""
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth import email_allowed, make_blob, read_blob
from app.main import create_app

SECRET = "s" * 40


@pytest.fixture
def gsettings(settings):
    return replace(settings, google_client_id="cid.apps.googleusercontent.com", google_client_secret="gsecret",
                   google_allowed_emails="owner@gmail.com, @corp.com", session_secret=SECRET)


def google_app(gsettings, profile=None, token_status=200, userinfo_status=200):
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        if "oauth2.googleapis.com/token" in str(req.url):
            return httpx.Response(token_status, json={"access_token": "at-123"})
        return httpx.Response(userinfo_status, json=profile if profile is not None else {"email": "owner@gmail.com", "email_verified": True})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return TestClient(create_app(gsettings, http_client=http), follow_redirects=False), calls


def start(client):
    """Begin the flow; return the state Google would echo back."""
    r = client.get("/api/auth/google/login")
    assert r.status_code == 303
    return r, parse_qs(urlparse(r.headers["location"]).query)["state"][0]


def callback(client, state, code="auth-code"):
    return client.get("/api/auth/google/callback", params={"code": code, "state": state})


def test_disabled_without_config_or_without_allowlist(settings):
    c = TestClient(create_app(settings), follow_redirects=False)
    assert c.get("/api/auth/google/login").status_code == 404 and c.get("/api/auth/google/callback").status_code == 404
    no_list = replace(settings, google_client_id="x", google_client_secret="y", admin_password="p" * 14, session_secret=SECRET)
    c2 = TestClient(create_app(no_list), follow_redirects=False)  # credentials but empty allowlist => never open to everyone
    assert c2.get("/api/auth/google/login").status_code == 404 and c2.get("/api/auth/me").json()["google_enabled"] is False


def test_production_refuses_google_without_allowlist(settings):
    bad = replace(settings, env="production", admin_password="p" * 14, session_secret=SECRET, webhook_secret="w" * 20,
                  google_client_id="x", google_client_secret="y")
    with pytest.raises(RuntimeError, match="GOOGLE_ALLOWED_EMAILS"):
        create_app(bad)


def test_login_redirects_to_google_with_pkce_state_and_lax_cookie(gsettings):
    c, _ = google_app(gsettings)
    r, state = start(c)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert r.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q["client_id"] == [gsettings.google_client_id] and q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["http://testserver/api/auth/google/callback"] and q["scope"] == ["openid email"]
    assert len(state) >= 20 and q["code_challenge"][0]
    cookie = r.headers["set-cookie"].lower()
    assert "woocrm_oauth" in cookie and "samesite=lax" in cookie and "httponly" in cookie
    assert c.get("/api/auth/me").json()["google_enabled"] is True


def test_explicit_redirect_uri_setting_is_used(gsettings):
    c, _ = google_app(replace(gsettings, google_redirect_uri="https://woocrm.onrender.com/api/auth/google/callback"))
    r, _ = start(c)
    assert parse_qs(urlparse(r.headers["location"]).query)["redirect_uri"] == ["https://woocrm.onrender.com/api/auth/google/callback"]


def test_successful_sign_in_creates_a_session_and_sends_pkce_verifier(gsettings):
    c, calls = google_app(gsettings)
    _, state = start(c)
    r = callback(c, state)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "woocrm_session" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert c.get("/api/customers").status_code == 200 and c.get("/api/auth/me").json()["user"] == "owner@gmail.com"
    token_req = next(x for x in calls if "token" in str(x.url))
    body = parse_qs(token_req.content.decode())
    assert body["code_verifier"][0] and body["grant_type"] == ["authorization_code"] and body["client_secret"] == ["gsecret"]
    assert next(x for x in calls if "userinfo" in str(x.url)).headers["authorization"] == "Bearer at-123"


def test_domain_allowlist_entry_admits_anyone_at_that_domain(gsettings):
    c, _ = google_app(gsettings, profile={"email": "Jane@Corp.com", "email_verified": True})
    _, state = start(c)
    assert callback(c, state).headers["location"] == "/"
    assert c.get("/api/auth/me").json()["user"] == "jane@corp.com"


@pytest.mark.parametrize("profile", [
    {"email": "stranger@gmail.com", "email_verified": True},      # valid Google user, not on the allowlist
    {"email": "owner@gmail.com", "email_verified": False},        # unverified address must never count
    {"email": "owner@gmail.com"},                                 # missing verification flag
    {"email": "attacker@evilcorp.com", "email_verified": True},   # look-alike domain must not match "@corp.com"
    {},
])
def test_unlisted_or_unverified_accounts_are_rejected(gsettings, profile):
    c, _ = google_app(gsettings, profile=profile)
    _, state = start(c)
    r = callback(c, state)
    assert r.status_code == 303 and r.headers["location"].startswith("/?login_error=")
    assert c.get("/api/customers").status_code == 401


def test_state_mismatch_or_missing_cookie_is_rejected_before_calling_google(gsettings):
    c, calls = google_app(gsettings)
    start(c)
    assert callback(c, "forged-state").headers["location"].startswith("/?login_error=")
    fresh, calls2 = google_app(gsettings)  # no oauth cookie at all (e.g. attacker-initiated callback)
    assert callback(fresh, "whatever").headers["location"].startswith("/?login_error=")
    assert not calls and not calls2 and c.get("/api/customers").status_code == 401


def test_cancelled_consent_and_google_errors_fail_safely(gsettings):
    c, _ = google_app(gsettings)
    start(c)
    assert "login_error" in c.get("/api/auth/google/callback", params={"error": "access_denied"}).headers["location"]
    for kw in ({"token_status": 400}, {"userinfo_status": 500}):
        bad, _ = google_app(gsettings, **kw)
        _, state = start(bad)
        assert callback(bad, state).headers["location"].startswith("/?login_error=")
        assert bad.get("/api/customers").status_code == 401


def test_oauth_state_blob_is_signed_and_expires():
    blob = make_blob(SECRET, {"state": "a"})
    assert read_blob(SECRET, blob)["state"] == "a"
    assert read_blob("other" * 10, blob) is None and read_blob(SECRET, blob[:-2] + "xx") is None and read_blob(SECRET, None) is None
    assert read_blob(SECRET, make_blob(SECRET, {"state": "a"}, ttl=-1)) is None


def test_email_allowed_rules():
    allow = ["owner@gmail.com", "@corp.com"]
    assert email_allowed("OWNER@gmail.com", allow) and email_allowed("x@corp.com", allow)
    assert not email_allowed("x@notcorp.com", allow) and not email_allowed("owner@gmail.com.evil.io", allow)
    assert not email_allowed("", allow) and not email_allowed("a@b.c", [])


def test_google_only_setup_cannot_be_entered_with_an_empty_password(gsettings):
    c, _ = google_app(gsettings)  # no ADMIN_PASSWORD configured: "admin" + "" must NOT log in
    r = c.post("/api/auth/login", json={"username": "admin", "password": ""})
    assert r.status_code == 401 and c.get("/api/customers").status_code == 401
    assert c.get("/api/auth/me").json()["password_login"] is False


def test_password_login_still_works_alongside_google(gsettings):
    c, _ = google_app(replace(gsettings, admin_password="correct-horse-battery"))
    assert c.post("/api/auth/login", json={"username": "admin", "password": "correct-horse-battery"}).status_code == 200
    assert c.get("/api/customers").status_code == 200
