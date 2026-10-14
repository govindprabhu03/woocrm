# Deployment Guide

## Recommended platform: **Render** (Docker web service)
Why: WooCRM is a long-running Python server that must receive public **webhooks** and keep a database. Render deploys the included `Dockerfile` straight from GitHub, gives free HTTPS + a stable URL (needed for Woo webhooks), auto-generates secrets (`render.yaml`), health-checks `/health`, and offers managed Postgres when you outgrow SQLite.

| Platform | Fit | Notes |
|---|---|---|
| **Render** ⭐ | Best for hackathon + small prod | Free tier sleeps after ~15 min idle (cold start ~30 s) and has an ephemeral disk; paid tier (~$7/mo) stays awake. Add managed Postgres for durable data |
| Railway | Very good | Great DX, usage-based pricing, built-in Postgres; no permanent free tier |
| Fly.io | Good for production | Docker + volumes + regions near customers (Mumbai); more ops knowledge needed |
| Azure Container Apps / AWS App Runner | Best if the org is already there | Strong governance/networking; heavier setup and cost |
| Vercel / Netlify | **Not suitable** | Serverless functions: no persistent SQLite, cold starts, poor fit for a stateful FastAPI app |

## Deploy to Render (≈10 minutes)
1. Push the repo to GitHub (done). In Render: **New → Blueprint** → select the repo (grant Render access to the private repo). `render.yaml` provisions the service.
2. Open the service → **Environment**: read the generated `ADMIN_PASSWORD`; (optional) add `GEMINI_API_KEY` (free from https://aistudio.google.com/app/apikey) for the Gemini-powered agent.
3. Open the URL, sign in as `admin`.
4. **Connect a real store:** set `WOO_URL`, `WOO_KEY`, `WOO_SECRET` (WooCommerce → Settings → Advanced → REST API, read access), set `ALLOW_SIMULATION=0`. In WooCommerce → Settings → Advanced → Webhooks create `order.created`, `order.updated`, `customer.created` pointing to `https://<your-app>/api/webhooks/woocommerce`, **Secret = `WOO_WEBHOOK_SECRET`**.
5. Press **Sync** once to import history (quiet — sends no emails).
6. **Durable data:** create a Render Postgres and set `DATABASE_URL` (the `postgres://` form is accepted).

## Production readiness checklist
- [x] Authentication, signed webhooks, fail-closed config, security headers (see `SECURITY.md`)
- [x] Health endpoint, non-root container, pinned dependencies, CI (tests, coverage gate, bandit, pip-audit, docker build)
- [x] Structured auth logging (login ok/failed/blocked)
- [ ] Use Postgres + automated backups (SQLite on free tier resets on redeploy)
- [x] Real email via SendGrid (`SENDGRID_API_KEY`, `MAIL_FROM`; `SENDGRID_SANDBOX=1` to test safely). Still do: verify your sender/domain in SendGrid (SPF/DKIM)
- [ ] Alerting/uptime monitor on `/health` (UptimeRobot/Better Stack); error tracking (Sentry)
- [ ] Custom domain + rotate secrets periodically; consider SSO/MFA for multi-user
- [ ] Data-protection: retention policy and customer data deletion on request

## Local production-like run
```bash
ENV=production ADMIN_PASSWORD='a-long-password-123' SESSION_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" \
WOO_WEBHOOK_SECRET='change-me-to-16+chars' uvicorn app.main:create_app --factory --port 8100
```
(Cookies are `Secure` in production, so use HTTPS or a reverse proxy; for plain-HTTP local testing use `ENV=development` with `ADMIN_PASSWORD` set.)
