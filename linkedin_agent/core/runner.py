"""Execute one task end to end: safety checks, content, browser, persistence, sequence advance."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import Settings
from ..models import (
    READ_ONLY_ACTIONS,
    AccountState,
    Action,
    Campaign,
    ErrorKind,
    LeadRecord,
    LeadStage,
    Task,
    TaskResult,
    TaskStatus,
)
from ..ports import (
    AccountStore,
    ActionLog,
    BrowserProvider,
    LeadStore,
    ReviewQueue,
    TaskExecutor,
    TaskQueue,
    TextLLM,
)
from . import messages as msg
from . import sequence as seqeng
from .errors import classify_error, classify_result
from .limits import account_age_days, effective_cap, remaining
from .status_map import apply_result, is_success, normalize_reply_check, normalize_status
from .tasks import build_prompt
from .timezone import resolve_tz, schedule_in_window

logger = logging.getLogger(__name__)

BREAKER_HOURS = 48
MAX_CONSECUTIVE_FAILURES = 3
MAX_ATTEMPTS = 3
# Infrastructure failures (browser gone, laptop slept mid-task) do not consume a task's
# attempts; they have their own, higher ceiling so a permanently broken browser still ends.
MAX_CRASH_RETRIES = 6
# A sleep that overshoots by this much means the machine was asleep, not the loop.
WAKE_GAP_S = 60.0
NETWORK_WAIT_MAX_S = 600.0
NETWORK_PROBE_EVERY_S = 15.0
RETRY_DELAY = timedelta(minutes=10)
IDENTICAL_WINDOW = timedelta(days=7)
# The loop is meant to run for weeks. An unexpected exception in one iteration is logged
# and the loop backs off and continues; only this many in a row means something is
# systematically broken and it is better to stop than to spin.
MAX_LOOP_ERRORS = 10
LOOP_ERROR_BACKOFF_S = 30.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Deps:
    settings: Settings
    queue: TaskQueue
    leads: LeadStore
    log: ActionLog
    accounts: AccountStore
    review: ReviewQueue
    executor: TaskExecutor
    pool: BrowserProvider
    campaigns: dict[str, Campaign]
    text_llm: TextLLM | None = None
    clock: Callable[[], datetime] = utcnow
    rng: random.Random = field(default_factory=random.Random)
    sleep: Callable[[float], Any] = asyncio.sleep
    wall: Callable[[], float] = time.time
    # Monotonic time does not advance while the machine is suspended; wall time does. The
    # difference between the two is how long the laptop slept, wherever in the loop it
    # happened, including in the middle of a browser action.
    mono: Callable[[], float] = time.monotonic
    # Campaign file mtimes as of the last tick; None until the first tick records them.
    campaign_files: dict[str, float] | None = None
    network_ok: Callable[[], Awaitable[bool]] | None = None  # default: probe linkedin.com


@dataclass
class Outcome:
    status: TaskStatus
    result: TaskResult | None = None
    note: str = ""
    stop: bool = False  # the loop should stop (session expired)
    parked_until: datetime | None = None


class ContentError(Exception):
    pass


def start_of_local_day(now: datetime, tz_name: str) -> datetime:
    tz = resolve_tz(tz_name)
    local = now.astimezone(tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def next_local_day(now: datetime, tz_name: str, hour: int = 8) -> datetime:
    tz = resolve_tz(tz_name)
    local = now.astimezone(tz)
    nxt = (local + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return nxt.astimezone(timezone.utc)


async def usage(deps: Deps, account: str, action: Action, now: datetime) -> tuple[int, int]:
    day = await deps.log.count(
        account, action, start_of_local_day(now, deps.settings.default_timezone)
    )
    week = await deps.log.count(account, action, now - timedelta(days=7))
    return day, week


def caps_for(
    deps: Deps, acct: AccountState, action: Action, now: datetime
) -> tuple[int, int | None]:
    return effective_cap(
        action,
        account_age_days(acct.first_action_at, now),
        acct.governor_state,
        deps.settings.user_cap(action.value),
        deps.settings.tier,
    )


# ── content preparation ───────────────────────────────────────────────────


async def prepare_content(
    task: Task, lead: LeadRecord | None, campaign: Campaign | None, deps: Deps
) -> None:
    """Fill task.params with the final text for this action. Mutates task."""
    p = task.params
    if task.action in (Action.MESSAGE, Action.INMAIL):
        if not p.get("text"):
            name = p.get("template")
            if not (name and lead and campaign):
                raise ContentError("message has no text and no template")
            rendered = await msg.render_message(str(name), lead, campaign, deps.text_llm)
            p["text"] = rendered.text
            p["render_warnings"] = rendered.warnings
        if task.action == Action.INMAIL and not p.get("subject"):
            sname = p.get("subject_template")
            if not (sname and lead and campaign):
                raise ContentError("inmail has no subject")
            p["subject"] = (
                await msg.render_message(str(sname), lead, campaign, deps.text_llm)
            ).text
        task.body_hash = msg.body_hash(str(p["text"]))
    elif task.action == Action.CONNECT:
        if "note" not in p:
            name = p.get("note_template")
            if name and lead and campaign:
                p["note"] = (
                    await msg.render_message(str(name), lead, campaign, deps.text_llm)
                ).text
            else:
                p["note"] = ""
    elif task.action == Action.COMMENT_POST:
        if not p.get("text"):
            if not (lead and campaign and deps.text_llm):
                raise ContentError("comment has no text and no LLM to draft one")
            post = _post_for_task(task, lead)
            if post is None:
                raise ContentError("no post to comment on")
            draft, problems = await msg.draft_comment(
                post, lead, campaign, deps.text_llm, int(p.get("max_sentences", 3))
            )
            if draft is None:
                raise ContentError("comment draft rejected: " + "; ".join(problems))
            p["text"] = draft
    elif task.action == Action.CHECK_REPLIES:
        if lead and lead.last_message_text and not p.get("last_message_snippet"):
            p["last_message_snippet"] = lead.last_message_text[:80]


def _post_for_task(task: Task, lead: LeadRecord) -> Any:
    url = task.params.get("post_url")
    if url:
        for post in lead.posts:
            if post.url == url:
                return post
    text = task.params.get("post_text")
    if text:
        for post in lead.posts:
            if post.text == text:
                return post
    return seqeng.pick_post(lead, str(task.params.get("pick", "newest")))


# ── the main entry point ──────────────────────────────────────────────────


async def process_task(task: Task, deps: Deps) -> Outcome:
    now = deps.clock()
    account = task.account
    acct = await deps.accounts.get(account)

    # 1. account-level gates
    if acct.session_expired_at:
        return await _park(task, deps, "session_expired", None, stop=True)
    if acct.tripped_until and acct.tripped_until > now:
        return await _park(task, deps, f"circuit_breaker: {acct.trip_reason}", acct.tripped_until)

    lead = await deps.leads.get(task.lead_id) if task.lead_id else None
    campaign = deps.campaigns.get(lead.campaign) if lead else None

    # 2. caps (read-only checks are not capped by the governor but still counted)
    day_count, week_count = await usage(deps, account, task.action, now)
    if remaining(day_count, week_count, caps_for(deps, acct, task.action, now)) <= 0:
        until = next_local_day(now, deps.settings.default_timezone)
        not_after: datetime | None = None
        if lead and campaign and task.step_id:
            # Tomorrow 08:00 may be a Saturday or before the window opens; put the task
            # back inside its step's window from then, rather than letting it run at the
            # first moment the cap resets.
            try:
                step = campaign.step(task.step_id)
                tz = resolve_tz(lead.timezone, campaign.default_timezone)
                until, not_after = schedule_in_window(
                    step.window, until, tz, campaign.window_specs or None
                )
            except (KeyError, RuntimeError, ValueError):
                pass
        return await _park(task, deps, "rate_limited", until, not_after=not_after)

    # 3. lead, campaign, content
    if lead and lead.stage == LeadStage.PAUSED:
        return await _finish(task, deps, TaskStatus.SKIPPED, TaskResult(status="lead_paused"))
    try:
        await prepare_content(task, lead, campaign, deps)
        # Building the prompt validates every parameter (URLs, required text). A task that
        # cannot be phrased can never succeed, so it is skipped here rather than handed to
        # the browser, retried three times and counted against the breaker.
        try:
            build_prompt(task.action, task.profile_url, task.params)
        except ValueError as e:
            raise ContentError(f"invalid task parameters: {e}") from e
    except ContentError as e:
        logger.warning("Task %s skipped: %s", task.id, e)
        outcome = await _finish(
            task, deps, TaskStatus.SKIPPED, TaskResult(status="no_content", error=str(e))
        )
        if lead and task.step_id and campaign:
            await _advance_sequence(
                lead, campaign, task, TaskResult(status="skipped"), deps, now, soft=True
            )
        return outcome
    await deps.queue.update(task)

    # 4. identical-copy guard
    if (
        task.action in (Action.MESSAGE, Action.INMAIL)
        and task.body_hash
        and not task.params.get("allow_identical")
    ):
        if await deps.queue.body_sent_recently(account, task.body_hash, now - IDENTICAL_WINDOW):
            return await _finish(
                task,
                deps,
                TaskStatus.FAILED,
                TaskResult(
                    status="identical_body",
                    error="same text was sent to another lead in the last 7 days",
                ),
            )

    # 5. reply check before any message in a sequence
    if (
        task.action == Action.MESSAGE
        and lead
        and campaign
        and task.step_id
        and not task.params.get("skip_reply_check")
    ):
        replied = await _check_replied_first(task, lead, campaign, deps, now)
        if replied is not None:
            return replied

    # 6. browser
    try:
        browser = await deps.pool.get_browser(account)
    except Exception as e:
        deps.pool.mark_browser_dead()
        logger.exception("Browser start failed")
        return await _fail(
            task,
            deps,
            acct,
            lead,
            campaign,
            TaskResult(status="browser_error", error=str(e)[:200]),
            ErrorKind.CRASH,
            now,
        )

    # 7. execute (a retried message first checks the thread so a lost confirmation
    #    does not turn into a duplicate send)
    result: TaskResult | None = None
    if task.action in (Action.MESSAGE, Action.INMAIL) and task.attempts > 1:
        result = await _already_sent(task, browser, deps, now)
    try:
        if result is None:
            result = await deps.executor.execute(task, browser)
    except Exception as e:
        kind = classify_error(e)
        logger.warning("Task %s raised %s: %s", task.id, kind, str(e)[:200])
        if kind == ErrorKind.CRASH:
            deps.pool.mark_browser_dead()
        else:
            await deps.pool.cleanup_pages()
        return await _fail(
            task,
            deps,
            acct,
            lead,
            campaign,
            TaskResult(status="error", error=str(e)[:300]),
            kind,
            now,
        )

    # A status the tables do not know must never route nowhere. Error-shaped results are
    # left for classify_result; anything else is snapped to a known status or made a
    # retryable failure.
    if classify_result(result) is None:
        result = normalize_status(task.action, result)

    if task.action == Action.CONNECT and result.status == "cannot_connect":
        result = await _verify_cannot_connect(task, browser, deps, now)

    if task.action == Action.CHECK_REPLIES:
        result = normalize_reply_check(result, lead)

    result_kind = classify_result(result)
    if result_kind == ErrorKind.SESSION_EXPIRED:
        # A model looking at an empty page also says "login required". Ask the browser.
        alive = await deps.pool.verify_session(account)
        if alive is True:
            logger.warning("Task reported login_required but the feed loads; treating as crash")
            result = TaskResult(status="error", error="false login_required: browser unstable")
            result_kind = ErrorKind.CRASH
    if result_kind is not None:
        if result_kind == ErrorKind.CRASH:
            deps.pool.mark_browser_dead()
        else:
            await deps.pool.cleanup_pages()
        return await _fail(task, deps, acct, lead, campaign, result, result_kind, now)

    # 8. success path
    ok = is_success(task.action, result)
    await deps.log.record(account, task.action, task.lead_id, ok, result.status, now)
    acct.consecutive_failures = 0
    acct.first_action_at = acct.first_action_at or now
    await deps.accounts.save(acct)
    deps.pool.increment_task_count()

    if lead is not None:
        lead = apply_result(
            lead, task.action, result, now, campaign.default_timezone if campaign else "UTC"
        )
        if task.action in (Action.MESSAGE, Action.INMAIL) and result.status == "sent":
            lead.last_message_text = str(task.params.get("text", ""))[:500]
            prior = result.data.get("prior_reply_text")
            if prior is not None:
                lead.prior_reply_text = str(prior)[:200] or None
        await deps.leads.update(lead)
        if campaign and task.step_id:
            await _advance_sequence(lead, campaign, task, result, deps, now)

    await deps.pool.cleanup_pages()
    return await _finish(task, deps, TaskStatus.DONE, result)


# What a read-only connection check turns a "cannot_connect" verdict into.
CONNECT_VERIFY_MAP: dict[str, str] = {
    "pending": "already_pending",
    "connected": "already_connected",
}


async def _verify_cannot_connect(task: Task, browser: Any, deps: Deps, now: datetime) -> TaskResult:
    """The model reported no Connect button. That verdict ends the lead's sequence for good,
    so take a second, read-only look before believing it.

    pending/connected -> the matching success status; not_connected -> a retryable failure
    (the button is there, the request was simply not sent); no_option -> cannot_connect
    stands; anything else -> retry later rather than give up on the lead."""
    probe = Task(
        lead_id=task.lead_id,
        action=Action.CHECK_CONNECTION,
        profile_url=task.profile_url,
        account=task.account,
    )
    try:
        check = await deps.executor.execute(probe, browser)
    except Exception as e:
        logger.warning("cannot_connect verification raised: %s", str(e)[:120])
        return TaskResult(status="failed", error=f"cannot_connect unverified: {str(e)[:120]}")
    await deps.log.record(
        task.account, Action.CHECK_CONNECTION, task.lead_id, True, check.status, now
    )
    logger.info("cannot_connect verification for %s: %s", task.profile_url, check.status)
    if check.status in CONNECT_VERIFY_MAP:
        return TaskResult(
            status=CONNECT_VERIFY_MAP[check.status], data={"verified_by": "check_connection"}
        )
    if check.status == "not_connected":
        return TaskResult(
            status="failed", error="connect button is present but the request was not sent"
        )
    if check.status == "no_option":
        return TaskResult(status="cannot_connect", data={"verified_by": "check_connection"})
    if classify_result(check) is not None:  # login_required, restricted, plain failure
        return check
    return TaskResult(
        status="failed", error=f"cannot_connect unverified: check returned {check.status!r}"
    )


async def _already_sent(task: Task, browser: Any, deps: Deps, now: datetime) -> TaskResult | None:
    """Before re-sending a message whose first attempt failed, look at the thread. If a
    message beginning with the same text is already there, report it as sent instead."""
    text = str(task.params.get("text") or "")
    probe_text = text.strip().splitlines()[0][:80] if text.strip() else ""
    if not probe_text:
        return None
    probe = Task(
        lead_id=task.lead_id,
        action=Action.CHECK_REPLIES,
        profile_url=task.profile_url,
        account=task.account,
        params={"probe_text": probe_text},
    )
    try:
        check = await deps.executor.execute(probe, browser)
    except Exception as e:
        logger.warning("Duplicate check before retry failed, sending anyway: %s", str(e)[:120])
        return None
    await deps.log.record(task.account, Action.CHECK_REPLIES, task.lead_id, True, check.status, now)
    if check.status == "already_sent":
        logger.info("Message to %s was already delivered; not sending again", task.profile_url)
        return TaskResult(status="sent", data={"verified_by": "thread_check"})
    if classify_result(check) is not None:  # login_required, restricted: surface it
        return check
    return None


async def _check_replied_first(
    task: Task, lead: LeadRecord, campaign: Campaign, deps: Deps, now: datetime
) -> Outcome | None:
    """Run a read-only reply check before sending a message. Returns an Outcome if the
    message must not be sent, else None."""
    if not lead.last_message_at:
        return None  # nothing sent yet, nothing to check
    probe = Task(
        lead_id=lead.id,
        action=Action.CHECK_REPLIES,
        profile_url=task.profile_url,
        account=task.account,
        params={"last_message_snippet": (lead.last_message_text or "")[:80]},
    )
    try:
        browser = await deps.pool.get_browser(task.account)
        result = await deps.executor.execute(probe, browser)
    except Exception as e:
        logger.warning("Pre-send reply check failed, sending anyway: %s", str(e)[:120])
        return None
    result = normalize_reply_check(result, lead)
    await deps.log.record(task.account, Action.CHECK_REPLIES, lead.id, True, result.status, now)
    if result.status == "replied":
        lead2 = apply_result(lead, Action.CHECK_REPLIES, result, now)
        await deps.leads.update(lead2)
        seq = await deps.leads.get_sequence(lead.id)
        if seq:
            seq.step_id, seq.next_due_at = None, None
            seq.history.append(
                {
                    "step_id": task.step_id,
                    "task_id": task.id,
                    "result": "replied_before_send",
                    "at": now.isoformat(),
                }
            )
            await deps.leads.save_sequence(seq)
        return await _finish(
            task, deps, TaskStatus.SKIPPED, TaskResult(status="replied_before_send")
        )
    return None


async def _advance_sequence(
    lead: LeadRecord,
    campaign: Campaign,
    task: Task,
    result: TaskResult,
    deps: Deps,
    now: datetime,
    soft: bool = False,
) -> None:
    seq = await deps.leads.get_sequence(lead.id)
    if seq is None or seq.step_id is None or seq.step_id != task.step_id:
        return
    step = campaign.step(seq.step_id)
    if task.action == Action.VISIT and seq.branch is None:
        seq.branch = seqeng.decide_branch(lead, campaign)
    if soft:
        adv = seqeng.skip_current(seq, campaign, now, deps.rng)
    else:
        adv = seqeng.advance(seq, campaign, step, result, now, deps.rng, task_id=task.id)
    await deps.leads.save_sequence(adv.seq)
    if adv.ended_stage is not None:
        fresh = await deps.leads.get(lead.id) or lead
        if fresh.stage != LeadStage.PAUSED:
            fresh.stage = adv.ended_stage
            await deps.leads.update(fresh)


async def _park(
    task: Task,
    deps: Deps,
    reason: str,
    until: datetime | None,
    stop: bool = False,
    not_after: datetime | None = None,
) -> Outcome:
    """Put the task back in the queue for later without counting an attempt."""
    task.status = TaskStatus.QUEUED
    task.attempts = max(0, task.attempts - 1)
    task.started_at = None
    if until is not None:
        task.not_before = until
        if not_after is not None:
            task.not_after = not_after
        elif task.not_after is not None and task.not_after <= until:
            task.not_after = None  # the scheduler re-windows it; better late than lost
    await deps.queue.update(task)
    return Outcome(status=TaskStatus.QUEUED, note=reason, stop=stop, parked_until=until)


async def _finish(task: Task, deps: Deps, status: TaskStatus, result: TaskResult | None) -> Outcome:
    task.status = status
    task.result = result.model_dump(mode="json") if result else None
    await deps.queue.finish(task.id, result, status)
    return Outcome(status=status, result=result)


async def _fail(
    task: Task,
    deps: Deps,
    acct: AccountState,
    lead: LeadRecord | None,
    campaign: Campaign | None,
    result: TaskResult,
    kind: ErrorKind,
    now: datetime,
) -> Outcome:
    result.error_kind = kind
    stop = False
    note = kind.value
    if kind == ErrorKind.SESSION_EXPIRED:
        acct.session_expired_at = now
        stop = True
        note = "session expired: run `linkedin-agent login`"
    elif kind == ErrorKind.RESTRICTED:
        acct.tripped_until = now + timedelta(hours=BREAKER_HOURS)
        acct.trip_reason = f"LinkedIn restriction signal: {result.error or result.status}"[:200]
        acct.consecutive_failures = 0
        note = f"circuit breaker tripped for {BREAKER_HOURS}h"
    elif kind == ErrorKind.OTHER:
        acct.consecutive_failures += 1
        if acct.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            acct.tripped_until = now + timedelta(hours=BREAKER_HOURS)
            acct.trip_reason = (
                f"{MAX_CONSECUTIVE_FAILURES} consecutive failures: {result.error or result.status}"[
                    :200
                ]
            )
            acct.consecutive_failures = 0
            note = (
                f"circuit breaker tripped for {BREAKER_HOURS}h "
                f"after {MAX_CONSECUTIVE_FAILURES} failures"
            )
    # crash: infra, not LinkedIn — no breaker, no counter
    await deps.accounts.save(acct)
    await deps.log.record(task.account, task.action, task.lead_id, False, result.status, now)

    if kind == ErrorKind.CRASH:
        # Give the attempt back: the browser or the machine failed, not the action.
        task.attempts = max(0, task.attempts - 1)
        crashes = int(task.params.get("_crash_retries", 0)) + 1
        task.params["_crash_retries"] = crashes
        retryable = crashes <= MAX_CRASH_RETRIES
        note = f"{note} ({crashes}/{MAX_CRASH_RETRIES} browser retries)"
    else:
        retryable = kind == ErrorKind.OTHER and task.attempts < MAX_ATTEMPTS
    if retryable:
        task.status = TaskStatus.QUEUED
        task.started_at = None
        task.not_before = now + RETRY_DELAY
        if task.not_after is not None and task.not_after <= task.not_before:
            task.not_after = None
        task.result = result.model_dump(mode="json")
        await deps.queue.update(task)
        return Outcome(
            status=TaskStatus.QUEUED,
            result=result,
            note=(
                f"{note}; retrying"
                if kind == ErrorKind.CRASH
                else f"{note}; retry {task.attempts}/{MAX_ATTEMPTS}"
            ),
            stop=stop,
        )

    outcome = await _finish(task, deps, TaskStatus.FAILED, result)
    if lead and campaign and task.step_id:
        # stall the sequence on this step; `retry` re-arms it
        seq = await deps.leads.get_sequence(lead.id)
        if seq and seq.step_id == task.step_id:
            seq.next_due_at = None
            seq.history.append(
                {
                    "step_id": task.step_id,
                    "task_id": task.id,
                    "result": "failed",
                    "at": now.isoformat(),
                }
            )
            await deps.leads.save_sequence(seq)
    outcome.note, outcome.stop = note, stop
    return outcome


# ── the loop ──────────────────────────────────────────────────────────────


def pacing_delay(deps: Deps, action: Action) -> float:
    if deps.settings.fast_test:
        return deps.rng.uniform(5, 12)
    if action in READ_ONLY_ACTIONS:
        return deps.rng.uniform(8, 25)
    return deps.rng.uniform(deps.settings.min_delay_s, deps.settings.max_delay_s)


def tick_interval(deps: Deps) -> int:
    return (
        min(deps.settings.tick_interval_s, 20)
        if deps.settings.fast_test
        else deps.settings.tick_interval_s
    )


class SleepWatch:
    """How long the machine was suspended since the last look.

    Wall-clock time keeps running through a suspend; monotonic time does not (macOS and
    Linux). The drift between the two since the last call is time spent asleep, wherever
    in the loop the suspend happened — a pacing nap, a scheduler tick, or halfway through
    a browser action."""

    def __init__(self, deps: Deps) -> None:
        self._deps = deps
        self._wall = deps.wall()
        self._mono = deps.mono()

    def slept(self) -> float:
        wall, mono = self._deps.wall(), self._deps.mono()
        drift = (wall - self._wall) - (mono - self._mono)
        self._wall, self._mono = wall, mono
        return max(0.0, drift)


async def run_loop(
    deps: Deps,
    account: str,
    *,
    once: bool = False,
    on_event: Callable[[str], None] | None = None,
    tick: Callable[[], Any] | None = None,
    max_tasks: int | None = None,
) -> int:
    """Claim and process tasks until the queue is drained (once=True) or forever.

    `tick` is the scheduler callback (materialise due steps); it is called at start and
    every settings.tick_interval_s. Returns the number of tasks processed.

    Built to be left running: one bad iteration is logged and survived, a suspended
    laptop is noticed wherever it happened, and an expired session waits for `login`
    instead of ending the run."""
    emit = on_event or (lambda s: None)
    processed = 0
    last_tick = 0.0
    loop = asyncio.get_event_loop()
    watch = SleepWatch(deps)
    loop_errors = 0
    session_announced = False

    async def woke(gap: float) -> None:
        nonlocal last_tick
        await _after_wake(deps, gap, emit)
        last_tick = 0.0  # the world moved on: reschedule immediately

    async def nap(seconds: float) -> None:
        """Sleep, and notice if the machine slept far longer than asked."""
        before = deps.wall()
        await deps.sleep(seconds)
        overshoot = deps.wall() - before - seconds
        gap = max(overshoot, watch.slept())
        if gap > WAKE_GAP_S:
            await woke(gap)

    async def do_tick() -> None:
        nonlocal last_tick
        if tick is not None:
            await tick()
        last_tick = loop.time()

    async def survive(what: str, exc: Exception) -> bool:
        """Log an unexpected failure; True to carry on, False when it is time to stop."""
        nonlocal loop_errors
        loop_errors += 1
        logger.exception("%s failed (%d/%d in a row)", what, loop_errors, MAX_LOOP_ERRORS)
        emit(f"{what} failed: {type(exc).__name__}: {str(exc)[:160]}")
        if loop_errors >= MAX_LOOP_ERRORS or once:
            emit(f"{loop_errors} consecutive errors; stopping so the problem gets looked at")
            return False
        await nap(LOOP_ERROR_BACKOFF_S * min(loop_errors, 4))
        return True

    try:
        await do_tick()
    except Exception as e:
        if not await survive("scheduler tick", e):
            raise
    while True:
        now = deps.clock()
        if max_tasks is not None and processed >= max_tasks:
            break
        if tick is not None and loop.time() - last_tick >= tick_interval(deps):
            try:
                await do_tick()
            except Exception as e:
                if not await survive("scheduler tick", e):
                    raise
                continue
        try:
            acct = await deps.accounts.get(account)
            if acct.session_expired_at:
                # `login` clears this from another process. Wait for it: a detached run
                # that exited here would stay down long after the user signed back in.
                if once:
                    emit("session expired — run `linkedin-agent login`")
                    break
                if not session_announced:
                    emit("session expired — run `linkedin-agent login`; waiting for it")
                    session_announced = True
                await nap(tick_interval(deps))
                continue
            if session_announced:
                session_announced = False
                deps.pool.mark_browser_dead()  # the profile has new cookies; reopen it
                emit("login detected; resuming")
            if acct.tripped_until and acct.tripped_until > now:
                wait = min(
                    (acct.tripped_until - now).total_seconds(), deps.settings.tick_interval_s
                )
                emit(
                    "circuit breaker tripped until "
                    f"{acct.tripped_until.isoformat(timespec='minutes')}: {acct.trip_reason}"
                )
                if once:
                    break
                await nap(max(1.0, wait))
                continue
            task = await deps.queue.claim_next(account, now)
            if task is None:
                if once:
                    break
                await deps.pool.maybe_close_idle(deps.settings.idle_browser_timeout_s)
                await nap(min(25, tick_interval(deps)))
                continue
            outcome = await process_task(task, deps)
            processed += 1
            loop_errors = 0
            emit(_format(task, outcome))
            # A suspend in the middle of the action shows up here, not in a nap.
            gap = watch.slept()
            if gap > WAKE_GAP_S:
                await woke(gap)
            if outcome.stop and once:
                break
            if outcome.status in (TaskStatus.DONE, TaskStatus.FAILED):
                await nap(pacing_delay(deps, task.action))
        except Exception as e:
            if not await survive("run loop iteration", e):
                raise
    return processed


async def _after_wake(deps: Deps, gap_s: float, emit: Callable[[str], None]) -> None:
    """The machine was asleep: the browser's connection is almost certainly stale and the
    network may still be coming back. Restart the browser and wait for LinkedIn to answer
    before claiming anything, instead of burning a task and a model call to find out."""
    emit(f"resumed after about {int(gap_s // 60)} min asleep; checking network")
    deps.pool.mark_browser_dead()
    probe = deps.network_ok or _linkedin_reachable
    waited = 0.0
    while not await probe():
        if waited >= NETWORK_WAIT_MAX_S:
            emit("network still unavailable after 10 min; continuing anyway")
            return
        await deps.sleep(NETWORK_PROBE_EVERY_S)
        waited += NETWORK_PROBE_EVERY_S
    if waited:
        emit(f"network back after {int(waited)}s")


async def _linkedin_reachable() -> bool:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            r = await client.head("https://www.linkedin.com/")
        return r.status_code < 500
    except Exception:
        return False


def _format(task: Task, outcome: Outcome) -> str:
    res = outcome.result.status if outcome.result else outcome.status.value
    who = task.params.get("lead_name") or task.lead_id or task.profile_url
    extra = f" · {outcome.note}" if outcome.note else ""
    return f"{task.action.value:<16} {who:<28} {res}{extra}"
