# 2-minute video script

**0:00–0:15 Problem** — "Store owners run WooCommerce but lose track of who their customers are and when to talk to them. WooCRM integrates a CRM with WooCommerce: customer data, order tracking, communication workflows — plus an AI agent."

**0:15–0:40 Customers** — Dashboard: click *Sync from WooCommerce*. Show segments bar (VIP, at-risk, churned…). Open Customers → click a VIP: LTV, RFM, order timeline, tags/notes.

**0:40–1:10 Live order tracking + workflows** — Click *Simulate new order*: a signed webhook goes through HMAC verification → Communications shows confirmation email. Click *Simulate order shipped*: timeline updates (processing → completed) and a "complete" email fires. Mention idempotency + dedupe.

**1:10–1:40 Agentic AI** — AI Agent tab: ask "Who are my VIP customers?" (tool calls visible). Click *Run autonomous retention agent* → it drafts win-back emails; go to Communications and approve one. "Agents draft, humans send."

**1:40–2:00 Engineering + AI usage** — Show architecture diagram (Adapter/Strategy/Observer), `pytest` 55 + 13 browser tests passing, and AI_USAGE.md: "Built with Claude Code from plan to tests; the product's agent uses Gemini function calling. Deployable via Docker/Render."
