# Architecture

## Flow
1. **Ingest** — `full_sync` pulls customers/orders via the `WooClient` port; webhooks hit `/api/webhooks/woocommerce` (HMAC verified, delivery-id idempotent). Both call the same `upsert_*` functions.
2. **Derive** — `refresh_metrics` recomputes order_count / LTV / recency and the segment (`domain/segmentation.py`, pure).
3. **React** — upserts publish events (`customer.created`, `order.created`, `order.status:<s>`, `segment.changed`) on the `EventBus`; `WorkflowEngine` matches enabled workflows, checks conditions, renders templates, writes a deduped `Communication`.
4. **Assist** — the agent calls `CrmTools` (same code for Claude tool-use and the rule fallback). Drafts are `status=draft` until a human approves.

## Data model
`Customer` 1—* `Order`; `Customer` 1—* `Communication` *—1 `Workflow`; `WebhookEvent` (idempotency ledger).

## Extension points
- Real email: implement `EmailSender.send`.
- Other commerce platforms: implement `WooClient`.
- New trigger: publish an `Event` with a new type string; workflows can target it immediately.

## Trade-offs
Synchronous event bus (simple, transactional) over a queue; SQLite default for zero-setup demo; single tenant.
