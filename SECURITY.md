# Security

## Threat model & controls
| Threat | Control | Verified by |
|---|---|---|
| Unauthorised access to customer data | Admin login; every `/api/*` route (except the signed webhook and `/auth/*`) requires a valid session | `test_api_requires_login…` |
| Session forgery / tampering | HMAC-SHA256 signed token with 8h expiry, constant-time comparison; cookie is `HttpOnly`, `SameSite=Strict`, `Secure` in production | `test_tampered_forged_and_expired_tokens_rejected`, `test_login_flow_and_logout` |
| Google sign-in abuse (any Google user becoming admin, CSRF, forged callbacks) | Authorization-code flow + PKCE; random `state` bound to a signed short-lived cookie; **mandatory email allowlist** (exact address or `@domain`); `email_verified` must be true; look-alike domains rejected; feature stays off without an allowlist (production refuses to boot misconfigured); an unset admin password can never be matched | `tests/test_google.py` |
| Password brute force | 5 failed logins / 5 min per IP → HTTP 429 (even for the right password while locked); failures are logged | `test_brute_force_lockout`, `test_limiter_window_expires` |
| Forged webhooks | `X-WC-Webhook-Signature` = base64(HMAC-SHA256(secret, body)), verified in constant time → 401 | `test_webhook_uses_hmac_not_session`, `test_webhook_rejects_bad_signature` |
| Webhook replay / retries | Delivery-ID ledger (each ID processed once); workflow emails deduped by unique key | `test_new_order_webhook_triggers_workflow_and_dedupes` |
| Malformed / huge payloads | 1 MB limit (413); bad JSON or shape → 422 (never 500), delivery not recorded so a corrected retry works | `test_webhook_oversized…`, `test_webhook_malformed…` |
| CSRF | `SameSite=Strict` cookie + reject cross-origin `Origin` on state-changing requests | `test_cross_origin_post_blocked…` |
| XSS / clickjacking / sniffing | UI escapes all dynamic data; JSON-only API; CSP (`frame-ancestors 'none'`, `connect-src 'self'`), `X-Frame-Options`, `nosniff`, `Referrer-Policy`, HSTS in production | `test_security_headers`, `test_stored_xss_payload…` |
| SQL injection | SQLAlchemy parameterised queries only; no string-built SQL | `test_sql_injection_attempts_are_inert` |
| Unbounded input | Pydantic length/shape limits on notes, tags, workflow fields, chat messages; workflow `trigger` is a strict pattern | `test_validation_limits` |
| Insecure deployment | **Fail-closed**: `ENV=production` refuses to start without `ADMIN_PASSWORD` (≥12), `SESSION_SECRET` (≥32), non-default `WOO_WEBHOOK_SECRET` (≥16); demo simulator off by default | `test_production_refuses_weak_config` |
| AI misuse / prompt injection via customer data | Agent tools are narrow; the agent can only **draft** — sending requires human approval in the UI; agent messages are length-capped | `test_tools_draft_never_sends`, `test_agent_retention_run…` |
| Vulnerable dependencies | Pinned versions; `pip-audit` + `bandit` run in CI | CI workflow |
| Container risk | Runs as non-root user, slim base image, healthcheck, no secrets baked in | `Dockerfile` |

## Secrets
Never committed (`.env` is git-ignored). Provide via environment variables (see `.env.example`). Generate with
`python -c "import secrets;print(secrets.token_urlsafe(48))"`. On Render, `render.yaml` auto-generates them.

## Known limitations (be honest in review)
- Single admin account (no roles/SSO/MFA) — fine for a store owner; add OIDC for teams.
- Login throttling is in-memory and per instance; use a shared store (Redis) if scaled horizontally.
- CSP allows `'unsafe-inline'` scripts because the SPA is a single inline file; move to external JS + nonces to tighten.
- Customer PII is stored in the DB: use managed Postgres with encrypted storage + backups; add a data-retention/erase workflow for GDPR/DPDP requests.
- Email delivery is simulated; real SMTP/SendGrid needs SPF/DKIM and unsubscribe handling.

## Reporting
Open a private security advisory on the GitHub repository.
