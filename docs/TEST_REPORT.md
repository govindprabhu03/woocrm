# QA & Test Report

Run date: 2026-10-14 · Python 3.12 · FastAPI 0.143 · SQLAlchemy 2.1

## Summary
| Check | Result |
|---|---|
| Automated tests | **55 passed + 13 browser tests passed, 0 failed** (unit 17, Woo-client contract 3, API end-to-end 9, security 18, email/SendGrid 8; Playwright/Chromium 13) |
| Code coverage | **96 %** of `app/` (CI gate ≥ 90 %) |
| Static security analysis (bandit) | **0 high / 0 medium**; 2 low findings reviewed and annotated (deterministic mock-data RNG; dev-only default secret rejected in production) |
| Dependency audit (pip-audit) | **No known vulnerabilities** |
| Load smoke (600 req, 25 concurrent, mixed endpoints, 1 uvicorn worker, SQLite) | **0 failures, 228 req/s, p50 72 ms, p95 300 ms** |
| Browser E2E (Playwright, real Chromium) | Login/logout, webhook→email flow, ship order, agent draft→approve, Ctrl+K, filters, workflow toggle persistence, stored-XSS inertness, all views without JS errors, 390px mobile overflow check |

Reproduce: `pytest --cov=app` · `bandit -r app` · `pip-audit -r requirements.txt` · `python scripts/smoke_load.py http://127.0.0.1:8100 600 25`

## Test strategy
- **Unit** — segmentation rules & RFM, payload mappers, HMAC signing, template rendering, workflow engine (conditions, dedupe, status transitions), agent tools, rule-based agent, Gemini function-calling loop (mock transport: tool execution, verbatim part echo, key-in-header, declarations shape, max steps, quota/network fallback).
- **Contract** — real `HttpWooClient` against a fake transport: pagination, Basic auth, `/wp-json/wc/v3` paths, HTTP errors surfaced (no silent partial data).
- **End-to-end (API)** — seeded import → idempotent re-sync → signed webhook → workflow email → dedupe on retry → draft/approve → agent.
- **Security/robustness** — see `SECURITY.md` table; includes SQL-injection strings, stored-XSS payload, oversized/malformed webhooks, forged/expired/tampered tokens, brute force, CSRF, production fail-closed config.

## Defects found by QA and fixed
| # | Finding | Severity | Fix |
|---|---|---|---|
| 1 | API had **no authentication** | Critical | Session auth on all admin routes; HMAC-only webhook |
| 2 | Malformed webhook (bad JSON / missing `id`) returned **HTTP 500** | High | Now 422, rolled back, delivery not recorded so a corrected retry succeeds |
| 3 | No webhook payload size limit | Medium | 1 MB cap → 413 |
| 4 | Unbounded inputs (notes, tags, workflow fields, chat) | Medium | Pydantic limits; strict trigger pattern |
| 5 | `/orders` and `/communications` ran **N+1 queries** (~200 extra queries/request) | Medium | Eager loading (`joinedload`); throughput 190 → 228 req/s, p50 100 → 72 ms |
| 6 | Real Woo HTTP client had **0 % test coverage** | Medium | Contract tests added (now 100 %) |
| 7 | Insecure defaults could reach production | High | `validate()` fail-closed in `ENV=production` |
| 8 | Load-test script itself was the bottleneck (new TLS context per request) | Info | Shared client; initial "9 req/s" was a measurement artefact, not a server limit |

## Manual UAT checklist
- [x] Sign in with wrong password → error; right password → dashboard; Sign out → login screen
- [x] Sync from WooCommerce is idempotent (counts unchanged)
- [x] New order → pipeline animates, confirmation email appears in Messages, no duplicates on replay
- [x] Ship order → timeline shows processing → completed, "complete" email created
- [x] Retention agent → drafts appear, only sent after **Approve & send**
- [x] Workflow toggle off → no email on next matching event
- [x] Customer notes/tags persist; XSS text shown as plain text
- [ ] Real WooCommerce store connection (needs a live store + keys) — **not tested here**
- [x] Docker image build — built in CI on every push
- [ ] Cross-browser / mobile device testing — only the embedded Chromium pane used

## Not covered / recommended next
Multi-instance concurrency with Postgres, soak testing, accessibility audit (axe), real-email deliverability.
