import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import auth
from app.api import public, router
from app.config import Settings
from app.db import make_session_factory
from app.integrations.mock_woo import MockWooStore
from app.integrations.woo import HttpWooClient
from app.services import sync
from app.services.email import build_sender
from app.services.events import EventBus
from app.services.workflows import WorkflowEngine, seed_default_workflows

STATIC = Path(__file__).parent / "static"
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
log = logging.getLogger("woocrm")


def create_app(settings: Settings | None = None, woo_client=None, sender=None, http_client=None) -> FastAPI:
    """App factory: wires the Woo adapter, event bus, workflow engine and DB (all injectable for tests)."""
    settings = settings or Settings()
    settings.validate()
    if not settings.auth_enabled:
        log.warning("Authentication is DISABLED (no ADMIN_PASSWORD). Development only.")
    app = FastAPI(title="WooCRM", version="1.0", description="CRM integrated with WooCommerce")
    st = app.state
    st.settings, st.session_factory, st.delivery = settings, make_session_factory(settings.database_url), 0
    st.sender = sender or build_sender(settings)
    st.woo = woo_client or (MockWooStore().seed() if settings.use_mock_store
                            else HttpWooClient(settings.woo_url, settings.woo_key, settings.woo_secret))
    st.bus = EventBus()
    st.limiter = auth.LoginLimiter()
    st.http = http_client or httpx.Client(timeout=10)  # outbound calls (Google OAuth)
    st.bus.subscribe(WorkflowEngine(st.sender).handle)

    with st.session_factory() as db:
        seed_default_workflows(db)
        if settings.auto_seed and settings.use_mock_store:
            from sqlalchemy import func, select

            from app.models import Customer

            if db.scalar(select(func.count()).select_from(Customer)) == 0:
                sync.full_sync(db, st.bus, st.woo, quiet=True)  # historical import: no emails

    @app.middleware("http")
    async def security(request, call_next):
        # CSRF defence in depth (cookie is SameSite=Strict): reject cross-origin state-changing browser requests
        origin = request.headers.get("origin")
        if (origin and request.method in ("POST", "PUT", "PATCH", "DELETE")
                and not request.url.path.startswith("/api/webhooks/")
                and urlparse(origin).netloc != request.headers.get("host")):
            return JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)
        resp = await call_next(request)
        resp.headers.update({"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff",
                             "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                             "Permissions-Policy": "camera=(), microphone=(), geolocation=()"})
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        if settings.production:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp

    app.include_router(auth.router)
    app.include_router(public)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.get("/", include_in_schema=False)(lambda: FileResponse(STATIC / "index.html"))
    app.get("/health", include_in_schema=False)(lambda: {"ok": True})
    return app

