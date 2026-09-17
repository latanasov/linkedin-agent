"""The browser-use executor: step budgets and the wall-clock bound on a task."""

import asyncio

import pytest

from linkedin_agent.adapters import browser_use_executor as ex
from linkedin_agent.core.errors import classify_error
from linkedin_agent.models import Action, ErrorKind, Task


def _task(action: Action = Action.MESSAGE) -> Task:
    return Task(
        lead_id="l1",
        action=action,
        profile_url="https://www.linkedin.com/in/janedoe/",
        account="default",
        params={"text": "hi"},
    )


def test_wall_clock_budget_is_capped_on_a_hosted_model_and_scales_on_a_local_one():
    assert ex.wall_clock_budget_s(16, None) == ex.WALL_CLOCK_CAP_S
    assert ex.wall_clock_budget_s(6, None) == 6 * ex.DEFAULT_STEP_TIMEOUT_S
    assert ex.wall_clock_budget_s(16, 600) == 16 * 600 + 60


async def test_the_deadline_does_not_wait_for_work_that_ignores_cancellation(monkeypatch):
    """The first version used asyncio.wait_for, which cancels and then AWAITS the
    unwinding; browser-use's event bus swallows the cancellation, so the timeout hung with
    it and a message task sat "running" for 194 minutes. The deadline must be ours alone."""
    started = asyncio.Event()
    refused = asyncio.Event()

    async def swallows_cancellation(*a, **k):
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            refused.set()  # exactly what the event bus does to us
        await asyncio.sleep(0.01)
        return {"status": "sent"}

    monkeypatch.setattr(ex, "run_linkedin_agent", swallows_cancellation)
    monkeypatch.setattr(ex, "wall_clock_budget_s", lambda steps, step: 0.05)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            ex.BrowserUseExecutor(llm=object()).execute(_task(), browser=object()),
            timeout=5,  # only bounds the test; the executor's own deadline is 0.05s
        )
    assert started.is_set()
    await asyncio.sleep(0.05)
    assert refused.is_set(), "the work refused to die, and the executor returned anyway"
    # In production that orphan is ended by killing the browser; the test loop cannot, so
    # drain it here rather than leave it to outlive the loop.
    leftovers = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in leftovers:
        t.cancel()
    await asyncio.gather(*leftovers, return_exceptions=True)


async def test_a_task_that_hangs_is_cut_off_and_reads_as_a_crash(monkeypatch):
    """Seen live: a message task "running" for two hours, the only browser held, every
    task behind it waiting, and the stale-task watchdog unable to run because the loop
    itself was stuck in the await."""

    async def hangs(*a, **k):
        await asyncio.sleep(60)

    monkeypatch.setattr(ex, "run_linkedin_agent", hangs)
    monkeypatch.setattr(ex, "wall_clock_budget_s", lambda steps, step: 0.05)
    with pytest.raises(asyncio.TimeoutError) as info:
        await ex.BrowserUseExecutor(llm=object()).execute(_task(), browser=object())
    assert "timed out" in str(info.value) and "hung" in str(info.value)
    # the runner classifies it as a crash: fresh browser, attempt given back, no breaker
    assert classify_error(info.value) == ErrorKind.CRASH


async def test_a_task_that_finishes_in_time_is_unaffected(monkeypatch):
    async def quick(*a, **k):
        return {"status": "sent"}

    monkeypatch.setattr(ex, "run_linkedin_agent", quick)
    out = await ex.BrowserUseExecutor(llm=object()).execute(_task(), browser=object())
    assert out.status == "sent"
