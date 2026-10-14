"""Browser E2E harness: boots the real app (auth ON, mock Woo store) on a free port and drives it with Chromium."""
import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from app.config import Settings
from app.main import create_app

PASSWORD = "e2e-Passw0rd-123"


def base_settings(**over) -> Settings:
    return Settings(**{**dict(database_url="sqlite://", woo_url="", admin_password=PASSWORD, session_secret="s" * 40,
                              webhook_secret="w" * 20, auto_seed=True, allow_simulation=True, sendgrid_api_key="",
                              mail_from="", gemini_api_key="", google_client_id="", google_client_secret="",
                              google_allowed_emails=""), **over})


def start_server(s: Settings):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(create_app(s), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    return f"http://127.0.0.1:{port}", srv, t


@pytest.fixture(scope="session")
def server():
    url, srv, t = start_server(base_settings())
    yield url
    srv.should_exit = True
    t.join(5)


@pytest.fixture(scope="session")
def google_server():
    url, srv, t = start_server(base_settings(google_client_id="cid.apps.googleusercontent.com",
                                             google_client_secret="gsecret", google_allowed_emails="owner@gmail.com"))
    yield url
    srv.should_exit = True
    t.join(5)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, server):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    pg.base, pg.dialogs, pg.js_errors = server, [], []
    pg.on("dialog", lambda d: (pg.dialogs.append(d.message), d.dismiss()))
    pg.on("pageerror", lambda e: pg.js_errors.append(str(e)))
    yield pg
    ctx.close()


@pytest.fixture
def authed(page):
    page.goto(page.base)
    page.fill("#lu", "admin")
    page.fill("#lp", PASSWORD)
    page.click("#login button")
    page.wait_for_selector("h1:has-text('understood')")
    return page
