import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///crm.db"))
    woo_url: str = field(default_factory=lambda: os.getenv("WOO_URL", ""))  # empty => built-in mock store
    woo_key: str = field(default_factory=lambda: os.getenv("WOO_KEY", ""))
    woo_secret: str = field(default_factory=lambda: os.getenv("WOO_SECRET", ""))
    webhook_secret: str = field(default_factory=lambda: os.getenv("WOO_WEBHOOK_SECRET", "demo-webhook-secret"))
    anthropic_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "claude-sonnet-5-5"))
    auto_seed: bool = field(default_factory=lambda: os.getenv("AUTO_SEED", "1") == "1")

    @property
    def use_mock_store(self) -> bool:
        return not self.woo_url
