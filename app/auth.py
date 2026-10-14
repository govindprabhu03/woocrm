"""Admin authentication: HMAC-signed, expiring session cookie (HttpOnly, SameSite=Strict) + login throttling."""
import base64
import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

log = logging.getLogger("woocrm.auth")
COOKIE = "woocrm_session"
TTL = 8 * 3600
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
    user = read_token(s.session_secret or s.admin_password, request.cookies.get(COOKIE))
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
    pass_ok = hmac.compare_digest(body.password.encode(), s.admin_password.encode())
    if not (s.auth_enabled and user_ok and pass_ok):
        limiter.fail(ip)
        log.warning("login failed ip=%s user=%r", ip, body.username[:30])
        raise HTTPException(401, "Invalid username or password")
    limiter.reset(ip)
    response.set_cookie(COOKIE, make_token(s.session_secret or s.admin_password, body.username), max_age=TTL,
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
        return {"auth_required": False, "authenticated": True, "user": "anonymous"}
    user = read_token(s.session_secret or s.admin_password, request.cookies.get(COOKIE))
    return {"auth_required": True, "authenticated": user is not None, "user": user}
