"""Settings loaded from environment / ~/.linkedin-agent/.env."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_HOME = Path("~/.linkedin-agent").expanduser()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LINKEDIN_AGENT_",
        env_file=(str(DEFAULT_HOME / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    home: Path = DEFAULT_HOME
    db_path: Path | None = None
    openrouter_api_key: str = ""
    # Where the models run. "openrouter" (default) needs the key; "ollama" runs local models
    # through Ollama at ollama_host and needs no key. Model names then follow Ollama's
    # naming (e.g. "qwen3.5:35b").
    llm_provider: Literal["openrouter", "ollama"] = "openrouter"
    ollama_host: str = "http://localhost:11434"
    ollama_timeout_s: int = 600  # local prompts of 40k tokens can take minutes per step
    browser_llm_model: str = "google/gemini-2.5-flash"
    text_llm_model: str = "google/gemini-2.5-flash"
    tier: Literal["free", "pro", "ultimate"] = "pro"
    daily_visit_limit: int | None = None
    daily_connect_limit: int | None = None
    daily_message_limit: int | None = None
    headless: bool = False
    chrome_path: Path | None = None
    browser_max_tasks: int = 20
    min_delay_s: int = 45
    max_delay_s: int = 180
    tick_interval_s: int = 300
    idle_browser_timeout_s: int = 1800
    proxy_url: str | None = None
    default_timezone: str = "UTC"
    account: str = "default"
    # Testing only: ignore send windows and per-prospect spacing, use short pacing and
    # ticks, so a compressed campaign can run end to end in one sitting. Caps still apply.
    fast_test: bool = False

    @field_validator("home", mode="before")
    @classmethod
    def _expand(cls, v: str | Path) -> Path:
        return Path(v).expanduser()

    @property
    def uses_openrouter(self) -> bool:
        return self.llm_provider == "openrouter"

    @property
    def llm_ready(self) -> bool:
        """True when the configured provider has what it needs to be called."""
        return bool(self.openrouter_api_key) if self.uses_openrouter else True

    @property
    def database_path(self) -> Path:
        return self.db_path or (self.home / "agent.db")

    @property
    def profiles_dir(self) -> Path:
        return self.home / "profiles"

    @property
    def campaigns_dir(self) -> Path:
        return self.home / "campaigns"

    def profile_dir(self, account: str) -> Path:
        return self.profiles_dir / account

    def user_cap(self, action: str) -> int | None:
        return {
            "visit": self.daily_visit_limit,
            "connect": self.daily_connect_limit,
            "message": self.daily_message_limit,
            "inmail": self.daily_message_limit,
        }.get(action)


def load_settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]
