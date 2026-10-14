# WooCRM — AI context
- Run: `uvicorn app.main:create_app --factory --port 8100` · Test: `pytest` (must stay green)
- Layers: `integrations/` (Woo adapter + mock) → `services/` (sync, events, workflows) → `domain/` (pure rules) → `api.py` → `static/index.html`; agent in `agent/`.
- Rules: no business logic in `api.py`; new Woo behaviour goes through the `WooClient` protocol; agent tools may only *draft* emails; every feature needs a unit test and an e2e test.
- Use `create_app(settings, woo_client, sender)` for DI in tests (`sqlite://` in-memory).
