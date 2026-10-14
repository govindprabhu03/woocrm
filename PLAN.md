# WooCRM — Plan (CRM ⇄ WooCommerce integration)

Hackathon topic: **CRM System Integration** — customer data management, order tracking, communication workflows.

## How the plan maps to the scoring criteria (in priority order)
| # | Criterion | What we do |
|---|-----------|-----------|
| 1 | **AI tool usage** | Built with Claude Code end-to-end; `AI_USAGE.md` logs how (planning, scaffolding, test generation, review loops); `CLAUDE.md` gives the repo AI context |
| 2 | **Codebase quality** | Adapter (WooClient), Strategy (EmailSender, LLM backend), Observer (EventBus→workflow engine), Factory (`create_app`), DI via FastAPI; pytest unit + end-to-end tests; GitHub Actions CI |
| 3 | **Documentation** | README with scope, assumptions, architecture diagram, run/deploy; `docs/ARCHITECTURE.md`; API docs at `/docs` |
| 4 | **Video (2 min)** | `docs/VIDEO_SCRIPT.md` — timed script following the live demo flow |
| 5 | **Agentic AI** | CRM Agent: Gemini function-calling loop over CRM tools (segment, search, draft, create workflow) + autonomous "retention run" that drafts win-back emails for human approval; works offline via rule-based fallback |
| 6 | **Execution quality** | Polished dashboard, Dockerfile + `render.yaml`, seeded Mock WooCommerce so it demos without a live store; real store via env vars |

## Scope
- **Customer data mgmt**: sync Woo customers (+ guest orders), computed LTV / order count / recency, auto segments (prospect, new, active, VIP, at-risk, churned), tags & notes.
- **Order tracking**: sync orders, status timeline, live webhook ingestion (HMAC-SHA256 verified, idempotent).
- **Communication workflows**: trigger→condition→action rules (e.g. order shipped → email), templated, deduped, full communication log, draft/approve flow.
- **Agent**: natural-language CRM assistant + retention run.

## Assumptions
- Woo REST API v3, consumer key/secret; webhooks `order.created|updated`, `customer.created`.
- Email is pluggable; default sender records "sent" in the log (no SMTP needed for demo).
- SQLite for demo; swap `DATABASE_URL` for Postgres.

## Milestones
1. Backend core + mock Woo + sync/webhooks → 2. Segmentation + workflow engine → 3. Agent → 4. Dashboard UI → 5. Tests/CI → 6. Docs, Docker, video script, deploy.
