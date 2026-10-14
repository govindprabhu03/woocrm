import os
from dataclasses import dataclass, field

DEFAULT_WEBHOOK_SECRET = "demo-webhook-secret"  # nosec B105 - dev default; validate() rejects it in production


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _database_url() -> str:
    url = _env("DATABASE_URL", "sqlite:///crm.db")
    return url.replace("postgres://", "postgresql://", 1) if url.startswith("postgres://") else url


@dataclass(frozen=True)
class Settings:
    env: str = field(default_factory=lambda: _env("ENV", "development"))  # development | production
    database_url: str = field(default_factory=_database_url)
    woo_url: str = field(default_factory=lambda: _env("WOO_URL"))  # empty => built-in mock store
    woo_key: str = field(default_factory=lambda: _env("WOO_KEY"))
    woo_secret: str = field(default_factory=lambda: _env("WOO_SECRET"))
    webhook_secret: str = field(default_factory=lambda: _env("WOO_WEBHOOK_SECRET", DEFAULT_WEBHOOK_SECRET))
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-2.5-flash"))
    auto_seed: bool = field(default_factory=lambda: _env("AUTO_SEED", "1") == "1")
    google_client_id: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_ID"))
    google_client_secret: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_SECRET"))
    google_allowed_emails: str = field(default_factory=lambda: _env("GOOGLE_ALLOWED_EMAILS"))  # "a@x.com, @corp.com"
    google_redirect_uri: str = field(default_factory=lambda: _env("GOOGLE_REDIRECT_URI"))  # default: derived from request
    sendgrid_api_key: str = field(default_factory=lambda: _env("SENDGRID_API_KEY"))
    mail_from: str = field(default_factory=lambda: _env("MAIL_FROM"))  # must be a SendGrid-verified sender
    sendgrid_sandbox: bool = field(default_factory=lambda: _env("SENDGRID_SANDBOX") == "1")
    admin_user: str = field(default_factory=lambda: _env("ADMIN_USER", "admin"))
    admin_password: str = field(default_factory=lambda: _env("ADMIN_PASSWORD"))  # empty => auth off (dev only)
    session_secret: str = field(default_factory=lambda: _env("SESSION_SECRET"))
    allow_simulation: bool = field(
        default_factory=lambda: _env("ALLOW_SIMULATION", "0" if _env("ENV") == "production" else "1") == "1")

    @property
    def production(self) -> bool:
        return self.env == "production"

    @property
    def use_mock_store(self) -> bool:
        return not self.woo_url

    @property
    def google_allowlist(self) -> list[str]:
        return [e.strip().lower() for e in self.google_allowed_emails.split(",") if e.strip()]

    @property
    def google_enabled(self) -> bool:
        """Google sign-in needs credentials AND an allowlist (otherwise any Google account would become admin)."""
        return bool(self.google_client_id and self.google_client_secret and self.google_allowlist)

    @property
    def signing_secret(self) -> str:
        return self.session_secret or self.admin_password or (self.google_client_secret if self.google_enabled else "")

    @property
    def auth_enabled(self) -> bool:
        return bool(self.admin_password) or self.google_enabled

    def validate(self) -> None:
        """Fail closed: refuse to boot in production with weak or missing security configuration."""
        if not self.production:
            return
        problems = []
        if len(self.admin_password) < 12:
            problems.append("ADMIN_PASSWORD must be set (>= 12 chars)")
        if len(self.session_secret) < 32:
            problems.append("SESSION_SECRET must be set (>= 32 chars)")
        if self.webhook_secret == DEFAULT_WEBHOOK_SECRET or len(self.webhook_secret) < 16:
            problems.append("WOO_WEBHOOK_SECRET must be a non-default secret (>= 16 chars)")
        if (self.google_client_id or self.google_client_secret) and not self.google_enabled:
            problems.append("Google sign-in needs GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_ALLOWED_EMAILS")
        if problems:
            raise RuntimeError("Insecure production configuration: " + "; ".join(problems))
