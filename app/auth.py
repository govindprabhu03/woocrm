"""Admin authentication: HMAC-signed, expiring session cookie (HttpOnly, SameSite=Strict) + login throttling."""
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

log = logging.getLogger("woocrm.auth")
COOKIE = "woocrm_session"
OAUTH_COOKIE = "woocrm_oauth"
TTL = 8 * 3600
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
router = APIRouter(prefix="/api/auth")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _sign(secret: str, payload: str) -> str:
    return _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())


def make_token(secret: str, user: str, now: float | None = None) -> str:
    payload = _b64(json.dumps({"u": user, "exp": int((now or time.time()) + TTL)}).encode())
    return f"{payload}.{_sign(secret, payload)}"


def read_token(secret: str, token: str | None, now: float | None = None) -> str | None:
    """Return the username if the token is authentic and unexpired, else None."""
    try:
        payload, sig = (token or "").split(".", 1)
        if not hmac.compare_digest(_sign(secret, payload), sig):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return data["u"] if data["exp"] > (now or time.time()) else None
    except (ValueError, KeyError, TypeError):
        return None


class LoginLimiter:
    """Blocks an IP after `max_fail` failed logins within `window` seconds (brute-force defence)."""

    def __init__(self, max_fail: int = 5, window: int = 300):
        self.max_fail, self.window, self._fails = max_fail, window, {}

    def _recent(self, key: str) -> list[float]:
        cutoff = time.time() - self.window
        self._fails[key] = [t for t in self._fails.get(key, []) if t > cutoff]
        return self._fails[key]

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.max_fail

    def fail(self, key: str) -> None:
        self._recent(key).append(time.time())

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)


def require_auth(request: Request) -> str:
    s = request.app.state.settings
    if not s.auth_enabled:
        return "anonymous"
    user = read_token(s.signing_secret, request.cookies.get(COOKIE))
    if user is None:
        raise HTTPException(401, "Authentication required")
    return user


class Login(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


@router.post("/login")
def login(body: Login, request: Request, response: Response):
    s, limiter = request.app.state.settings, request.app.state.limiter
    ip = request.client.host if request.client else "?"
    if limiter.blocked(ip):
        log.warning("login blocked (too many attempts) ip=%s", ip)
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")
    user_ok = hmac.compare_digest(body.username.encode(), s.admin_user.encode())
    pass_ok = bool(s.admin_password) and hmac.compare_digest(body.password.encode(), s.admin_password.encode())
    if not (user_ok and pass_ok):  # an unset password can never match (Google-only setups)
        limiter.fail(ip)
        log.warning("login failed ip=%s user=%r", ip, body.username[:30])
        raise HTTPException(401, "Invalid username or password")
    limiter.reset(ip)
    response.set_cookie(COOKIE, make_token(s.signing_secret, body.username), max_age=TTL,
                        httponly=True, samesite="strict", secure=s.production)
    log.info("login ok user=%s ip=%s", body.username, ip)
    return {"authenticated": True, "user": body.username}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"authenticated": False}


@router.get("/me")
def me(request: Request):
    s = request.app.state.settings
    if not s.auth_enabled:
        return {"auth_required": False, "authenticated": True, "user": "anonymous", "google_enabled": False}
    user = read_token(s.signing_secret, request.cookies.get(COOKIE))
    return {"auth_required": True, "authenticated": user is not None, "user": user, "google_enabled": s.google_enabled,
            "password_login": bool(s.admin_password)}


# ---------------- Google sign-in (OAuth 2.0 authorization-code flow + PKCE) ----------------
def make_blob(secret: str, data: dict, ttl: int = 600) -> str:
    payload = _b64(json.dumps({**data, "exp": int(time.time()) + ttl}).encode())
    return f"{payload}.{_sign(secret, payload)}"


def read_blob(secret: str, blob: str | None) -> dict | None:
    try:
        payload, sig = (blob or "").split(".", 1)
        if not hmac.compare_digest(_sign(secret, payload), sig):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return data if data["exp"] > time.time() else None
    except (ValueError, KeyError, TypeError):
        return None


def email_allowed(email: str, allowlist: list[str]) -> bool:
    """Exact address match, or '@domain.com' entries for a whole domain."""
    email = email.lower()
    return any(email == a or (a.startswith("@") and email.endswith(a)) for a in allowlist)


def _redirect_uri(request: Request) -> str:
    return request.app.state.settings.google_redirect_uri or str(request.base_url).rstrip("/") + "/api/auth/google/callback"


def _google_fail(message: str) -> RedirectResponse:
    resp = RedirectResponse(f"/?login_error={quote(message)}", status_code=303)
    resp.delete_cookie(OAUTH_COOKIE)
    return resp


@router.get("/google/login")
def google_login(request: Request):
    s = request.app.state.settings
    if not s.google_enabled:
        raise HTTPException(404, "Google sign-in is not configured")
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    url = GOOGLE_AUTH + "?" + urlencode({
        "client_id": s.google_client_id, "redirect_uri": _redirect_uri(request), "response_type": "code",
        "scope": "openid email", "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "prompt": "select_account"})
    resp = RedirectResponse(url, status_code=303)
    # Lax (not Strict): the cookie must come back on Google's top-level redirect to our callback
    resp.set_cookie(OAUTH_COOKIE, make_blob(s.signing_secret, {"state": state, "verifier": verifier}), max_age=600,
                    httponly=True, samesite="lax", secure=s.production)
    return resp


@router.get("/google/callback")
def google_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    s = request.app.state.settings
    if not s.google_enabled:
        raise HTTPException(404, "Google sign-in is not configured")
    if error or not code or not state:
        return _google_fail("Google sign-in was cancelled")
    blob = read_blob(s.signing_secret, request.cookies.get(OAUTH_COOKIE))
    if not blob or not hmac.compare_digest(str(blob.get("state", "")), state):  # CSRF / replay protection
        return _google_fail("Sign-in session expired. Please try again.")
    http = request.app.state.http
    try:
        tok = http.post(GOOGLE_TOKEN, data={
            "code": code, "client_id": s.google_client_id, "client_secret": s.google_client_secret,
            "redirect_uri": _redirect_uri(request), "grant_type": "authorization_code", "code_verifier": blob["verifier"]})
        tok.raise_for_status()
        info = http.get(GOOGLE_USERINFO, headers={"Authorization": f"Bearer {tok.json()['access_token']}"})
        info.raise_for_status()
        profile = info.json()
    except (httpx.HTTPError, KeyError, ValueError):
        log.warning("google sign-in failed during token exchange")
        return _google_fail("Google sign-in failed. Please try again.")
    email = str(profile.get("email") or "").lower()
    if profile.get("email_verified") is not True or not email_allowed(email, s.google_allowlist):
        log.warning("google sign-in rejected for %s", email[:60])
        return _google_fail("This Google account is not allowed to access WooCRM.")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE, make_token(s.signing_secret, email), max_age=TTL, httponly=True, samesite="strict",
                    secure=s.production)
    resp.delete_cookie(OAUTH_COOKIE)
    log.info("google login ok user=%s", email)
    return resp
