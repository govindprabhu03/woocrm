import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings():
    return Settings(database_url="sqlite://", woo_url="", anthropic_key="", auto_seed=True)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def empty_client(settings):
    from dataclasses import replace

    return TestClient(create_app(replace(settings, auto_seed=False)))
