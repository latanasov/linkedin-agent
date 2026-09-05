"""Build a Deps object from Settings: SQLite adapters, LLMs, browser pool, executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters.browser_use_executor import BrowserUseExecutor
from .adapters.sqlite import (
    Database,
    SqliteAccountStore,
    SqliteActionLog,
    SqliteLeadStore,
    SqliteReviewQueue,
    SqliteTaskQueue,
)
from .campaigns import load_all_user_campaigns
from .config import Settings
from .core.browser_pool import BrowserPool
from .core.runner import Deps
from .llm import make_browser_llm, make_text_llm
from .models import Campaign


class _LazyExecutor:
    """Defers browser-use + LLM construction until the first task actually runs."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._inner: BrowserUseExecutor | None = None

    async def execute(self, task: Any, browser: Any) -> Any:
        if self._inner is None:
            self._inner = BrowserUseExecutor(make_browser_llm(self._settings))
        return await self._inner.execute(task, browser)


@dataclass
class App:
    settings: Settings
    db: Database
    deps: Deps

    async def close(self) -> None:
        await self.deps.pool.shutdown()
        await self.db.close()


async def build_app(
    settings: Settings, *, campaigns: dict[str, Campaign] | None = None, need_llm: bool = True
) -> App:
    settings.home.mkdir(parents=True, exist_ok=True)
    db = await Database(settings.database_path).open()
    text_llm = None
    if need_llm and settings.llm_ready:
        text_llm = make_text_llm(settings)
    deps = Deps(
        settings=settings,
        queue=SqliteTaskQueue(db),
        leads=SqliteLeadStore(db),
        log=SqliteActionLog(db),
        accounts=SqliteAccountStore(db),
        review=SqliteReviewQueue(db),
        executor=_LazyExecutor(settings),
        pool=BrowserPool(settings),
        campaigns=campaigns if campaigns is not None else load_all_user_campaigns(settings),
        text_llm=text_llm,
    )
    return App(settings=settings, db=db, deps=deps)
