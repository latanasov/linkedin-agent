"""Shared fixtures: temp SQLite, fake executor/pool/LLM, a filled-in campaign, a fixed clock."""

from __future__ import annotations

import random
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from linkedin_agent.adapters.sqlite import (
    Database,
    SqliteAccountStore,
    SqliteActionLog,
    SqliteLeadStore,
    SqliteReviewQueue,
    SqliteTaskQueue,
)
from linkedin_agent.campaigns import BUILTIN_DIR, load_campaign
from linkedin_agent.config import Settings
from linkedin_agent.core.runner import Deps
from linkedin_agent.models import Action, Campaign, LeadRecord, PostRef, Task, TaskResult

# A Wednesday, 10:00 UTC — inside every window for a UTC lead.
NOW = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start: datetime = NOW) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> datetime:
        from datetime import timedelta

        self.now = self.now + timedelta(**kwargs)
        return self.now


class FakeExecutor:
    """Scripted results: by task id first, then by action, else a default success."""

    def __init__(self) -> None:
        self.by_task: dict[str, Any] = {}
        self.by_action: dict[Action, Any] = {}
        self.calls: list[Task] = []
        self.default: Callable[[Task], TaskResult] = lambda t: TaskResult(
            status=_default_status(t.action)
        )

    def script(self, action: Action, result: Any) -> None:
        self.by_action[action] = result

    async def execute(self, task: Task, browser: Any) -> TaskResult:
        self.calls.append(task)
        r = self.by_task.get(task.id, self.by_action.get(task.action))
        if r is None:
            return self.default(task)
        if isinstance(r, BaseException):
            raise r
        if callable(r):
            r = r(task)
        return TaskResult.from_raw(r)


def _default_status(action: Action) -> str:
    return {
        Action.VISIT: "ok",
        Action.FOLLOW: "followed",
        Action.LIKE_POST: "liked",
        Action.COMMENT_POST: "commented",
        Action.CONNECT: "sent",
        Action.CHECK_CONNECTION: "pending",
        Action.WITHDRAW_INVITE: "withdrawn",
        Action.MESSAGE: "sent",
        Action.INMAIL: "sent",
        Action.CHECK_REPLIES: "none",
    }[action]


class FakePool:
    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.dead = False
        self.tasks = 0
        self.cleanups = 0
        self.shutdowns = 0
        self.idle_closes = 0

    async def get_browser(self, account: str) -> Any:
        if self.fail:
            raise self.fail
        return object()

    async def open(self, account: str, *, headless: bool) -> Any:
        return object()

    def mark_browser_dead(self) -> None:
        self.dead = True

    def increment_task_count(self) -> None:
        self.tasks += 1

    async def cleanup_pages(self) -> None:
        self.cleanups += 1

    async def maybe_close_idle(self, idle_seconds: float) -> None:
        self.idle_closes += 1

    async def shutdown(self) -> None:
        self.shutdowns += 1

    session_alive: bool | None = None  # what verify_session answers
    verifications = 0

    async def verify_session(self, account: str) -> bool | None:
        self.verifications += 1
        return self.session_alive

    @property
    def has_active_browser(self) -> bool:
        return not self.dead


class FakeLLM:
    def __init__(self, replies: list[str] | None = None, fail: bool = False) -> None:
        self.replies = list(replies or [])
        self.prompts: list[str] = []
        self.fail = fail

    async def complete(self, prompt: str, *, temperature: float = 0.7) -> str:
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("llm down")
        if self.replies:
            return self.replies.pop(0)
        return "Your point about removing the approval step stuck with me."


def make_campaign(**overrides: Any) -> Campaign:
    c = load_campaign(BUILTIN_DIR / "default.yaml")
    data = c.model_dump()
    data.update(
        {
            "name": "test",
            "agent_name": "Alex",
            "company_name": "Northwind",
            "booking_link": "https://cal.com/alex/15min",
            "default_timezone": "UTC",
        }
    )
    data.update(overrides)
    return Campaign.model_validate(data)


def make_lead(**overrides: Any) -> LeadRecord:
    base: dict[str, Any] = {
        "campaign": "test",
        "linkedin_url": "https://www.linkedin.com/in/janedoe/",
        "first_name": "Jane",
        "last_name": "Doe",
        "company": "Acme",
        "title": "VP Engineering",
        "timezone": "UTC",
        "profile": {"headline": "VP Engineering at Acme"},
        "posts": [
            PostRef(
                url="https://www.linkedin.com/posts/janedoe_a-123",
                posted_days_ago=2,
                text="We cut onboarding time in half by removing the approval step. Support load dropped too.",
            ),
            PostRef(
                url="https://www.linkedin.com/posts/janedoe_b-456",
                posted_days_ago=9,
                text="Hiring two engineers for our platform team.",
            ),
        ],
    }
    base.update(overrides)
    return LeadRecord(**base)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path / "home",
        openrouter_api_key="test-key",
        default_timezone="UTC",
        tier="pro",
        min_delay_s=0,
        max_delay_s=0,
        tick_interval_s=1,
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    d = await Database(tmp_path / "test.db").open()
    try:
        yield d
    finally:
        await d.close()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def campaign() -> Campaign:
    return make_campaign()


@pytest.fixture
def executor() -> FakeExecutor:
    return FakeExecutor()


@pytest.fixture
def pool() -> FakePool:
    return FakePool()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
async def deps(
    settings: Settings,
    db: Database,
    clock: Clock,
    campaign: Campaign,
    executor: FakeExecutor,
    pool: FakePool,
    llm: FakeLLM,
) -> Deps:
    return Deps(
        settings=settings,
        queue=SqliteTaskQueue(db),
        leads=SqliteLeadStore(db),
        log=SqliteActionLog(db),
        accounts=SqliteAccountStore(db),
        review=SqliteReviewQueue(db),
        executor=executor,
        pool=pool,
        campaigns={campaign.name: campaign},
        text_llm=llm,
        clock=clock,
        rng=random.Random(42),
        sleep=_no_sleep,
    )
