"""Build a Deps object from Settings: SQLite adapters, LLMs, browser pool, executor."""

from __future__ import annotations

import logging
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
from .core.prompts import with_deadline
from .core.runner import Deps
from .llm import make_browser_llm, make_text_llm
from .models import Campaign

logger = logging.getLogger(__name__)


class _LazyExecutor:
    """Defers browser-use + LLM construction until the first task actually runs."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._inner: BrowserUseExecutor | None = None

    async def execute(self, task: Any, browser: Any) -> Any:
        if self._inner is None:
            llm_timeout_s, step_timeout_s = self._settings.browser_timeouts
            self._inner = BrowserUseExecutor(
                make_browser_llm(self._settings),
                llm_timeout_s=llm_timeout_s,
                step_timeout_s=step_timeout_s,
            )
        return await self._inner.execute(task, browser)


@dataclass
class App:
    settings: Settings
    db: Database
    deps: Deps

    async def close(self) -> None:
        # Both go through things that can hang (the browser's event bus, aiosqlite's
        # thread). A process on its way out must get out; a close that will not finish
        # is not worth more than the bound below.
        for what, coro, seconds in (
            ("browser shutdown", self.deps.pool.shutdown(), 45),
            ("database close", self.db.close(), 15),
        ):
            try:
                await with_deadline(coro, seconds, what)
            except Exception as e:
                logger.warning("%s did not finish: %s", what, str(e)[:120])


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
