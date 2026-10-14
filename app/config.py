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
    anthropic_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "claude-sonnet-5-5"))
    auto_seed: bool = field(default_factory=lambda: _env("AUTO_SEED", "1") == "1")
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
    def auth_enabled(self) -> bool:
        return bool(self.admin_password)

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
        if problems:
            raise RuntimeError("Insecure production configuration: " + "; ".join(problems))
