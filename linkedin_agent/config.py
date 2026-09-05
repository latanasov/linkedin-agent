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
    # Per-role overrides. The browser model is the expensive, demanding one and the text
    # model is easy, so "browser on OpenRouter, text on Ollama" is a sensible mix; unset,
    # both follow llm_provider.
    browser_llm_provider: Literal["openrouter", "ollama"] | None = None
    text_llm_provider: Literal["openrouter", "ollama"] | None = None
    ollama_host: str = "http://localhost:11434"
    ollama_timeout_s: int = 600  # local prompts of 40k tokens can take minutes per step
    # Per-model-call and per-step budgets for the browser model. None means browser-use
    # picks its own (60s and 120s), which only ever fits a hosted model.
    browser_llm_timeout_s: int | None = None
    browser_step_timeout_s: int | None = None
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
        """True when either model goes through OpenRouter."""
        return "openrouter" in (self.browser_provider, self.text_provider)

    @property
    def browser_provider(self) -> str:
        return self.browser_llm_provider or self.llm_provider

    @property
    def text_provider(self) -> str:
        return self.text_llm_provider or self.llm_provider

    @property
    def llm_ready(self) -> bool:
        """True when every configured provider has what it needs to be called."""
        return bool(self.openrouter_api_key) if self.uses_openrouter else True

    @property
    def browser_timeouts(self) -> tuple[int | None, int | None]:
        """(model call, whole step) budgets for the browser agent, in seconds.

        Explicit settings win. Otherwise a local model gets ollama_timeout_s per call and
        a little more per step, and a hosted one keeps browser-use's own defaults."""
        if self.browser_llm_timeout_s is not None or self.browser_step_timeout_s is not None:
            llm_t = self.browser_llm_timeout_s
            return llm_t, self.browser_step_timeout_s or (llm_t + 60 if llm_t else None)
        if self.browser_provider == "openrouter":
            return None, None
        return self.ollama_timeout_s, self.ollama_timeout_s + 60

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
