from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.config import Settings
from app.db import make_session_factory
from app.integrations.mock_woo import MockWooStore
from app.integrations.woo import HttpWooClient
from app.services import sync
from app.services.events import EventBus
from app.services.workflows import RecordingSender, WorkflowEngine, seed_default_workflows

STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, woo_client=None, sender=None) -> FastAPI:
    """App factory: wires the Woo adapter, event bus, workflow engine and DB (all injectable for tests)."""
    settings = settings or Settings()
    app = FastAPI(title="WooCRM", version="1.0", description="CRM integrated with WooCommerce")
    st = app.state
    st.settings, st.session_factory, st.delivery = settings, make_session_factory(settings.database_url), 0
    st.sender = sender or RecordingSender()
    st.woo = woo_client or (MockWooStore().seed() if settings.use_mock_store
                            else HttpWooClient(settings.woo_url, settings.woo_key, settings.woo_secret))
    st.bus = EventBus()
    st.bus.subscribe(WorkflowEngine(st.sender).handle)

    with st.session_factory() as db:
        seed_default_workflows(db)
        if settings.auto_seed and settings.use_mock_store:
            from sqlalchemy import func, select

            from app.models import Customer

            if db.scalar(select(func.count()).select_from(Customer)) == 0:
                sync.full_sync(db, st.bus, st.woo, quiet=True)  # historical import: no emails

    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.get("/", include_in_schema=False)(lambda: FileResponse(STATIC / "index.html"))
    app.get("/health", include_in_schema=False)(lambda: {"ok": True})
    return app

