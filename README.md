# WooCRM — CRM ⇄ WooCommerce Integration

A CRM that plugs into WooCommerce to give store owners **customer data management**, **order tracking** and **automated communication workflows**, plus an **agentic AI assistant** that does retention work for them.

![stack](https://img.shields.io/badge/FastAPI-SQLAlchemy-blue) ![tests](https://img.shields.io/badge/tests-44%20passing-green) ![coverage](https://img.shields.io/badge/coverage-96%25-green)

## Quick start (no WooCommerce store needed)
```bash
python -m venv .venv && .venv/Scripts/activate      # (source .venv/bin/activate on Linux/Mac)
pip install -r requirements.txt
uvicorn app.main:create_app --factory --port 8100
# open http://localhost:8100   (API docs: /docs)
pytest --cov=app                                    # 44 unit, contract, e2e and security tests (96% coverage)
```
By default the app uses a **built-in Mock WooCommerce store** (seeded with 40 customers / 80 orders in the real Woo REST v3 payload shape). To connect a real store set `WOO_URL`, `WOO_KEY`, `WOO_SECRET`, `WOO_WEBHOOK_SECRET` (see `.env.example`). Set `ANTHROPIC_API_KEY` to power the agent with Claude; without it a rule-based fallback is used.

## Scope
| Topic | Delivered |
|---|---|
| Customer data management | Sync customers + guest checkouts; lifetime value, order count, recency, RFM score; auto segments (prospect / new / active / VIP / at-risk / churned); tags & notes; 360° view |
| Order tracking | Full sync + live webhooks (`order.created/updated`); per-order status timeline; cancelled/refunded excluded from LTV |
| Communication workflows | Trigger → condition → action engine (email, tag); templating (`{{first_name}}`, `{{order_number}}`…); dedupe; full communication log; draft → human approval |
| Agentic AI | Chat agent with CRM tools (segment summary, search, customer detail, draft email, create workflow) and an autonomous **retention run** that drafts win-back emails for at-risk/churned customers |

## Architecture
```mermaid
flowchart LR
  Woo[WooCommerce REST v3 / Mock store] -- pull --> Sync[SyncService]
  Woo -- signed webhooks --> WH[/api/webhooks/woocommerce<br/>HMAC + idempotency/]
  WH --> Sync
  Sync --> DB[(SQLite / Postgres)]
  Sync -- domain events --> Bus[EventBus]
  Bus --> WF[WorkflowEngine] --> Comm[Communications log / EmailSender]
  DB --> API[FastAPI] --> UI[Dashboard SPA]
  API --> Agent[CRM Agent<br/>Claude tool-use | rules fallback] --> Tools[CrmTools] --> DB
```
Design patterns: **Adapter** (`WooClient` port: HTTP + Mock), **Strategy** (`EmailSender`, agent backend), **Observer** (`EventBus`), **Factory** (`create_app`), **Dependency Injection** (FastAPI `Depends`, injectable client/sender in tests). Pure domain logic (`domain/segmentation.py`) has no I/O. See `docs/ARCHITECTURE.md`.

## Key behaviours
- **Webhook security**: `X-WC-Webhook-Signature` = base64(HMAC-SHA256(secret, body)), verified in constant time; bad signature → 401.
- **Idempotency**: each `X-WC-Webhook-ID` is processed once (Woo retries); workflow emails are deduped by `(workflow, customer, order, event)` via a unique key.
- **Quiet first import**: the initial historical sync emits no events, so nobody gets spammed with old-order emails.
- **Human in the loop**: the agent can only *draft*; sending requires approval in the UI.

## Assumptions
1. WooCommerce REST API v3 with consumer key/secret; webhooks configured for `order.created`, `order.updated`, `customer.created`.
2. Customers are identified by email (guest checkouts are merged into a customer by billing email).
3. Revenue/LTV counts `completed`, `processing`, `on-hold` orders only.
4. Segment thresholds (VIP ≥ ₹8000 or ≥ 5 orders; at-risk > 60 days; churned > 120 days) are constants in `domain/segmentation.py`.
5. Email delivery is pluggable; the demo sender records messages in the log (implement `EmailSender.send` for SMTP/SendGrid).
6. SQLite for demo; set `DATABASE_URL` for Postgres. Single-tenant, no user auth (out of scope for the hackathon).

## Security & quality
Admin login (signed HttpOnly session cookie), HMAC-verified webhooks, brute-force lockout, CSRF/CSP/security headers, input limits, fail-closed production config. Details: [`SECURITY.md`](SECURITY.md) · QA results: [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md) · Deploying: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

To enable login set `ADMIN_PASSWORD` (and `SESSION_SECRET`); with `ENV=production` they are mandatory. Without them the app runs open for local development only.

## Operations
- **Docker**: `docker build -t woocrm . && docker run -p 8100:8100 woocrm`
- **Render**: `render.yaml` included — one-click Blueprint deploy with auto-generated secrets (recommended platform, see deployment guide).
- **CI**: `.github/workflows/ci.yml` runs pytest on every push.
- **Demo script**: `docs/VIDEO_SCRIPT.md`. **How AI was used**: `AI_USAGE.md`.

## API overview
`GET /api/stats · /customers · /customers/{id} · /orders · /workflows · /communications` · `POST /api/sync · /webhooks/woocommerce · /workflows · /communications/{id}/approve · /agent/chat · /agent/retention-run · /mock/simulate`
