import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from linkedin_agent.core import sequence as seqeng
from linkedin_agent.core.runner import (
    BREAKER_HOURS,
    MAX_ATTEMPTS,
    process_task,
    run_loop,
)
from linkedin_agent.models import Action, GovernorState, LeadStage, Task, TaskResult, TaskStatus
from tests.conftest import NOW, FakePool, make_lead


async def seed(deps, lead=None, step="warm.visit", branch=None, **lead_overrides):
    lead = lead or make_lead(**lead_overrides)
    await deps.leads.upsert_many([lead])
    camp = deps.campaigns["test"]
    s = seqeng.new_sequence(lead, camp, NOW)
    s.step_id, s.branch = step, branch
    await deps.leads.save_sequence(s)
    return lead, s


async def enqueue_step(deps, lead, step_id, **params):
    camp = deps.campaigns["test"]
    t = seqeng.build_task(camp.step(step_id), lead, camp, "default", NOW, params)
    await deps.queue.enqueue(t)
    return await deps.queue.claim(t.id, NOW)


# ── happy paths ──────────────────────────────────────────────────────────


async def test_visit_success_updates_lead_and_advances(deps, executor, pool):
    lead, _ = await seed(deps, posts=[], profile={}, stage=LeadStage.NEW)
    executor.script(
        Action.VISIT,
        {
            "status": "ok",
            "headline": "VP",
            "location": "Berlin",
            "posts": [{"url": "", "posted_days_ago": 1, "text": "hello world"}],
        },
    )
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.DONE
    lead2 = await deps.leads.get(lead.id)
    assert (
        lead2.stage == LeadStage.WARMING
        and lead2.profile["headline"] == "VP"
        and lead2.timezone == "Europe/Berlin"
    )
    seq = await deps.leads.get_sequence(lead.id)
    assert seq.branch == "posts" and seq.step_id == "warm.follow"
    assert await deps.log.count("default", Action.VISIT, NOW - timedelta(days=1)) == 1
    acct = await deps.accounts.get("default")
    assert acct.first_action_at == NOW
    assert pool.tasks == 1 and pool.cleanups == 1


async def test_connect_renders_note_template_and_records_invite(deps, executor, llm):
    lead, _ = await seed(deps, step="invite.posts", branch="posts")
    t = await enqueue_step(deps, lead, "invite.posts", note_template="connection_note")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.DONE
    sent = executor.calls[-1]
    assert sent.params["note"].startswith("Enjoyed your post on We cut onboarding")
    lead2 = await deps.leads.get(lead.id)
    assert lead2.stage == LeadStage.INVITED and lead2.invited_at == NOW
    assert (await deps.leads.get_sequence(lead.id)).step_id == "wait.accept"


async def test_quiet_branch_sends_blank_note(deps, executor):
    lead, _ = await seed(deps, step="invite.quiet", branch="quiet", posts=[])
    t = await enqueue_step(deps, lead, "invite.quiet", note_template="connection_note_quiet")
    await process_task(t, deps)
    assert executor.calls[-1].params["note"] == ""


async def test_message_renders_template_with_hook_and_sets_body_hash(deps, executor, llm):
    llm.replies = ["Your onboarding post was sharp."]
    lead, _ = await seed(
        deps, step="post.m1", branch="posts", stage=LeadStage.CONNECTED, connected_at=NOW
    )
    t = await enqueue_step(deps, lead, "post.m1", template="m1")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.DONE
    sent = executor.calls[-1]
    assert "Hi Jane, thanks for connecting.\nYour onboarding post was sharp." in sent.params["text"]
    stored = await deps.queue.get(t.id)
    assert stored.body_hash
    lead2 = await deps.leads.get(lead.id)
    assert lead2.stage == LeadStage.MESSAGING and lead2.last_message_text.startswith("Hi Jane")
    assert (await deps.leads.get_sequence(lead.id)).step_id == "post.r1"


async def test_identical_body_is_refused(deps, executor):
    a, _ = await seed(deps, step="post.m2", branch="posts", stage=LeadStage.CONNECTED)
    b = make_lead(linkedin_url="https://www.linkedin.com/in/bob/", first_name="Bob", company="Acme")
    await seed(deps, lead=b, step="post.m2", branch="posts")
    ta = await enqueue_step(deps, a, "post.m2", template="m2")
    assert (await process_task(ta, deps)).status == TaskStatus.DONE
    tb = await enqueue_step(deps, b, "post.m2", template="m2")
    out = await process_task(tb, deps)
    assert out.status == TaskStatus.FAILED and out.result.status == "identical_body"
    tc = await enqueue_step(deps, b, "post.m2", template="m2", allow_identical=True)
    assert (await process_task(tc, deps)).status == TaskStatus.DONE


async def test_message_is_skipped_when_prospect_already_replied(deps, executor):
    lead, _ = await seed(
        deps,
        step="post.m2",
        branch="posts",
        stage=LeadStage.MESSAGING,
        last_message_at=NOW - timedelta(days=3),
        last_message_text="Hi Jane, thanks",
    )
    executor.script(
        Action.CHECK_REPLIES, {"status": "replied", "last_reply_text": "Sure, let's talk"}
    )
    t = await enqueue_step(deps, lead, "post.m2", template="m2")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.SKIPPED and out.result.status == "replied_before_send"
    assert [c.action for c in executor.calls] == [Action.CHECK_REPLIES]
    lead2 = await deps.leads.get(lead.id)
    assert lead2.stage == LeadStage.REPLIED
    assert (await deps.leads.get_sequence(lead.id)).step_id is None


async def test_comment_is_drafted_when_review_is_off(deps, executor, llm):
    llm.replies = [
        "Removing the approval step is the part most teams skip. Did the support load move elsewhere?"
    ]
    lead, _ = await seed(deps, step="warm.comment", branch="posts")
    t = await enqueue_step(
        deps,
        lead,
        "warm.comment",
        pick="different_from_liked",
        post_url=lead.posts[0].url,
        post_text=lead.posts[0].text,
    )
    out = await process_task(t, deps)
    assert out.status == TaskStatus.DONE
    assert executor.calls[-1].params["text"].startswith("Removing the approval step")
    lead2 = await deps.leads.get(lead.id)
    assert lead2.posts[0].commented


async def test_comment_draft_rejected_skips_step_softly(deps, executor, llm):
    llm.replies = ["Great post!", "So true!"]
    lead, _ = await seed(deps, step="warm.comment", branch="posts")
    t = await enqueue_step(deps, lead, "warm.comment", post_url=lead.posts[0].url)
    out = await process_task(t, deps)
    assert out.status == TaskStatus.SKIPPED and out.result.status == "no_content"
    assert executor.calls == []
    assert (await deps.leads.get_sequence(lead.id)).step_id == "invite.posts"


# ── gates ────────────────────────────────────────────────────────────────


async def test_rate_limit_parks_task_until_tomorrow(deps, executor, clock):
    lead, _ = await seed(deps, step="invite.posts", branch="posts")
    acct = await deps.accounts.get("default")
    acct.first_action_at = NOW - timedelta(days=60)
    await deps.accounts.save(acct)
    for _ in range(20):
        await deps.log.record(
            "default", Action.CONNECT, None, True, "sent", NOW - timedelta(hours=1)
        )
    t = await enqueue_step(deps, lead, "invite.posts", note="")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.QUEUED and out.note == "rate_limited"
    stored = await deps.queue.get(t.id)
    assert (
        stored.status == TaskStatus.QUEUED
        and stored.not_before.date() == (NOW + timedelta(days=1)).date()
    )
    assert stored.attempts == 0 and executor.calls == []


async def test_ramp_caps_new_account(deps, executor):
    lead, _ = await seed(deps, step="invite.posts", branch="posts")
    for _ in range(5):  # week-1 connect cap is 20*0.25 = 5
        await deps.log.record(
            "default", Action.CONNECT, None, True, "sent", NOW - timedelta(hours=1)
        )
    t = await enqueue_step(deps, lead, "invite.posts", note="")
    assert (await process_task(t, deps)).note == "rate_limited"


async def test_governor_paused_blocks_invites_but_not_visits(deps, executor):
    lead, _ = await seed(deps, step="invite.posts", branch="posts")
    acct = await deps.accounts.get("default")
    acct.governor_state = GovernorState.PAUSED
    await deps.accounts.save_governor(acct)
    t = await enqueue_step(deps, lead, "invite.posts", note="")
    assert (await process_task(t, deps)).note == "rate_limited"
    v = await enqueue_step(deps, lead, "warm.visit")
    assert (await process_task(v, deps)).status == TaskStatus.DONE


async def test_breaker_tripped_parks_task(deps, executor):
    lead, _ = await seed(deps)
    acct = await deps.accounts.get("default")
    acct.tripped_until, acct.trip_reason = NOW + timedelta(hours=10), "test"
    await deps.accounts.save(acct)
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.QUEUED and "circuit_breaker" in out.note
    assert out.parked_until == NOW + timedelta(hours=10) and executor.calls == []


async def test_paused_lead_is_skipped(deps, executor):
    lead, _ = await seed(deps, stage=LeadStage.PAUSED)
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.SKIPPED and executor.calls == []


# ── failures ─────────────────────────────────────────────────────────────


async def test_session_expired_result_stops_and_flags_account(deps, executor):
    lead, _ = await seed(deps)
    executor.script(Action.VISIT, {"status": "failed", "error": "login_required"})
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.FAILED and out.stop
    acct = await deps.accounts.get("default")
    assert acct.session_expired_at == NOW
    assert (await deps.leads.get_sequence(lead.id)).next_due_at is None  # stalled
    # a second task is parked without running
    t2 = await enqueue_step(deps, lead, "warm.visit")
    out2 = await process_task(t2, deps)
    assert out2.status == TaskStatus.QUEUED and out2.stop


async def test_restriction_trips_breaker_immediately(deps, executor):
    lead, _ = await seed(deps, step="invite.posts", branch="posts")
    executor.script(Action.CONNECT, RuntimeError("LinkedIn says: unusual activity detected"))
    t = await enqueue_step(deps, lead, "invite.posts", note="")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.FAILED and "circuit breaker tripped" in out.note
    acct = await deps.accounts.get("default")
    assert acct.tripped_until == NOW + timedelta(hours=BREAKER_HOURS)
    assert (
        await deps.log.count("default", Action.CONNECT, NOW - timedelta(days=1)) == 0
    )  # not counted


async def test_crash_marks_pool_dead_and_retries_without_breaker(deps, executor, pool):
    lead, _ = await seed(deps)
    executor.script(Action.VISIT, RuntimeError("Target closed"))
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.QUEUED and "browser retries" in out.note and pool.dead
    acct = await deps.accounts.get("default")
    assert acct.tripped_until is None and acct.consecutive_failures == 0
    stored = await deps.queue.get(t.id)
    assert stored.not_before == NOW + timedelta(minutes=10)
    assert stored.attempts == 0 and stored.params["_crash_retries"] == 1  # attempt given back


async def test_browser_start_failure_is_infra(deps, executor, pool):
    pool.fail = RuntimeError("chromium not found")
    lead, _ = await seed(deps)
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.QUEUED and out.result.status == "browser_error" and pool.dead


async def test_three_plain_failures_trip_breaker_and_stall_sequence(deps, executor):
    lead, _ = await seed(deps)
    executor.script(Action.VISIT, RuntimeError("element not found"))
    t = await enqueue_step(deps, lead, "warm.visit")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        out = await process_task(t, deps)
        if attempt < MAX_ATTEMPTS:
            assert out.status == TaskStatus.QUEUED
            t = await deps.queue.get(t.id)
            t.status, t.attempts = TaskStatus.RUNNING, attempt + 1
            await deps.queue.update(t)
    assert out.status == TaskStatus.FAILED
    acct = await deps.accounts.get("default")
    assert acct.tripped_until == NOW + timedelta(hours=BREAKER_HOURS)
    assert "3 consecutive failures" in acct.trip_reason
    assert (await deps.leads.get_sequence(lead.id)).next_due_at is None


async def test_success_resets_failure_counter(deps, executor):
    lead, _ = await seed(deps)
    acct = await deps.accounts.get("default")
    acct.consecutive_failures = 2
    await deps.accounts.save(acct)
    t = await enqueue_step(deps, lead, "warm.visit")
    await process_task(t, deps)
    assert (await deps.accounts.get("default")).consecutive_failures == 0


async def test_one_off_task_without_lead(deps, executor):
    t = Task(
        action=Action.CHECK_CONNECTION,
        profile_url="https://www.linkedin.com/in/stranger/",
        account="default",
        created_at=NOW,
    )
    await deps.queue.enqueue(t)
    claimed = await deps.queue.claim(t.id, NOW)
    executor.script(Action.CHECK_CONNECTION, {"status": "connected"})
    out = await process_task(claimed, deps)
    assert out.status == TaskStatus.DONE and out.result.status == "connected"


# ── loop ─────────────────────────────────────────────────────────────────


async def test_run_loop_once_drains_claimable_tasks(deps, executor):
    lead, _ = await seed(deps)
    for _ in range(3):
        camp = deps.campaigns["test"]
        await deps.queue.enqueue(
            seqeng.build_task(camp.step("warm.visit"), lead, camp, "default", NOW)
        )
    events: list[str] = []
    ticks = 0

    async def tick():
        nonlocal ticks
        ticks += 1

    n = await run_loop(deps, "default", once=True, on_event=events.append, tick=tick)
    assert n == 3 and ticks == 1 and len(events) == 3
    assert (await deps.queue.depth("default"))["done"] == 3


async def test_run_loop_stops_on_session_expiry(deps, executor):
    lead, _ = await seed(deps)
    camp = deps.campaigns["test"]
    for _ in range(2):
        await deps.queue.enqueue(
            seqeng.build_task(camp.step("warm.visit"), lead, camp, "default", NOW)
        )
    executor.script(Action.VISIT, {"status": "failed", "error": "login_required"})
    n = await run_loop(deps, "default", once=True)
    assert n == 1
    assert (await deps.queue.depth("default"))["queued"] == 1


async def test_run_loop_respects_breaker_in_once_mode(deps, executor):
    acct = await deps.accounts.get("default")
    acct.tripped_until = NOW + timedelta(hours=1)
    await deps.accounts.save(acct)
    events: list[str] = []
    n = await run_loop(deps, "default", once=True, on_event=events.append)
    assert n == 0 and any("circuit breaker" in e for e in events)


# ── cannot_connect is verified before it ends a sequence ─────────────────


async def _connect_then_check(deps, executor, check_result):
    lead, _ = await seed(deps, step="invite.posts", branch="posts")
    executor.script(Action.CONNECT, {"status": "cannot_connect"})
    executor.script(Action.CHECK_CONNECTION, check_result)
    t = await enqueue_step(deps, lead, "invite.posts", note_template="connection_note")
    out = await process_task(t, deps)
    return lead, t, out


async def test_cannot_connect_becomes_already_pending_when_check_sees_pending(deps, executor):
    lead, t, out = await _connect_then_check(deps, executor, {"status": "pending"})
    assert out.status == TaskStatus.DONE and out.result.status == "already_pending"
    assert [c.action for c in executor.calls] == [Action.CONNECT, Action.CHECK_CONNECTION]
    lead2 = await deps.leads.get(lead.id)
    assert lead2.stage == LeadStage.INVITED and lead2.invited_at == NOW
    assert (await deps.leads.get_sequence(lead.id)).step_id == "wait.accept"
    # the probe is logged as a check, the connect as a success
    assert await deps.log.count("default", Action.CHECK_CONNECTION, NOW - timedelta(days=1)) == 1
    assert await deps.log.count("default", Action.CONNECT, NOW - timedelta(days=1)) == 1


async def test_cannot_connect_becomes_already_connected_when_check_sees_1st(deps, executor):
    lead, _, out = await _connect_then_check(deps, executor, {"status": "connected"})
    assert out.result.status == "already_connected"
    lead2 = await deps.leads.get(lead.id)
    assert lead2.stage == LeadStage.CONNECTED
    assert (await deps.leads.get_sequence(lead.id)).step_id == "post.m1"


async def test_cannot_connect_with_connect_button_present_is_retried(deps, executor):
    lead, t, out = await _connect_then_check(deps, executor, {"status": "not_connected"})
    assert out.status == TaskStatus.QUEUED and "retry 1" in out.note
    assert "not sent" in out.result.error
    lead2 = await deps.leads.get(lead.id)
    assert lead2.stage != LeadStage.CANNOT_CONTACT
    assert (await deps.leads.get_sequence(lead.id)).step_id == "invite.posts"


async def test_cannot_connect_stands_when_check_finds_no_option(deps, executor):
    lead, _, out = await _connect_then_check(deps, executor, {"status": "no_option"})
    assert out.status == TaskStatus.DONE and out.result.status == "cannot_connect"
    assert out.result.data["verified_by"] == "check_connection"
    lead2 = await deps.leads.get(lead.id)
    assert lead2.stage == LeadStage.CANNOT_CONTACT
    assert (await deps.leads.get_sequence(lead.id)).step_id is None


async def test_cannot_connect_is_retried_when_verification_raises(deps, executor):
    lead, _, out = await _connect_then_check(deps, executor, RuntimeError("dom snapshot"))
    assert out.status == TaskStatus.QUEUED and "unverified" in out.result.error
    assert (await deps.leads.get(lead.id)).stage != LeadStage.CANNOT_CONTACT


async def test_cannot_connect_verification_login_redirect_flags_session(deps, executor):
    lead, _, out = await _connect_then_check(
        deps, executor, {"status": "failed", "error": "login_required"}
    )
    assert out.stop is True
    assert (await deps.accounts.get("default")).session_expired_at == NOW
    assert (await deps.leads.get(lead.id)).stage != LeadStage.CANNOT_CONTACT


async def test_cannot_connect_verification_only_runs_for_connect(deps, executor):
    lead, _ = await seed(deps, step="warm.visit")
    executor.script(Action.VISIT, {"status": "cannot_connect"})
    t = await enqueue_step(deps, lead, "warm.visit")
    await process_task(t, deps)
    assert [c.action for c in executor.calls] == [Action.VISIT]


# ── a retried message checks the thread before sending again ─────────────


async def _failed_once(deps, executor):
    """First attempt of message 1 fails; return the task ready for its second attempt."""
    lead, _ = await seed(deps, step="post.m1", branch="posts")
    executor.script(Action.MESSAGE, {"status": "failed", "error": "send button not found"})
    t = await enqueue_step(deps, lead, "post.m1", template="m1")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.QUEUED and "retry 1" in out.note
    executor.calls.clear()
    executor.by_action.pop(Action.MESSAGE)
    t2 = await deps.queue.get(t.id)
    t2.not_before = None
    await deps.queue.update(t2)
    return lead, await deps.queue.claim(t.id, NOW)


async def test_retry_does_not_resend_a_message_already_in_thread(deps, executor):
    lead, t = await _failed_once(deps, executor)
    assert t.attempts == 2
    executor.script(Action.CHECK_REPLIES, lambda p: {"status": "already_sent"})
    out = await process_task(t, deps)
    assert out.status == TaskStatus.DONE and out.result.status == "sent"
    assert out.result.data["verified_by"] == "thread_check"
    assert [c.action for c in executor.calls] == [Action.CHECK_REPLIES]
    probe = executor.calls[0]
    assert probe.params["probe_text"].startswith("Hi Jane, thanks for connecting.")
    lead2 = await deps.leads.get(lead.id)
    assert lead2.stage == LeadStage.MESSAGING and lead2.last_message_at == NOW
    assert (await deps.leads.get_sequence(lead.id)).step_id == "post.r1"


async def test_retry_sends_when_thread_has_no_such_message(deps, executor):
    lead, t = await _failed_once(deps, executor)
    executor.script(Action.CHECK_REPLIES, {"status": "not_sent"})
    out = await process_task(t, deps)
    assert out.status == TaskStatus.DONE and out.result.status == "sent"
    assert [c.action for c in executor.calls] == [Action.CHECK_REPLIES, Action.MESSAGE]


async def test_retry_sends_when_duplicate_check_raises(deps, executor):
    lead, t = await _failed_once(deps, executor)
    executor.script(Action.CHECK_REPLIES, RuntimeError("panel did not open"))
    out = await process_task(t, deps)
    assert out.status == TaskStatus.DONE
    assert [c.action for c in executor.calls] == [Action.CHECK_REPLIES, Action.MESSAGE]


async def test_retry_duplicate_check_login_redirect_stops(deps, executor):
    lead, t = await _failed_once(deps, executor)
    executor.script(Action.CHECK_REPLIES, {"status": "failed", "error": "login_required"})
    out = await process_task(t, deps)
    assert out.stop is True and [c.action for c in executor.calls] == [Action.CHECK_REPLIES]


async def test_first_attempt_sends_without_duplicate_check(deps, executor):
    lead, _ = await seed(deps, step="post.m1", branch="posts")
    t = await enqueue_step(deps, lead, "post.m1", template="m1")
    assert t.attempts == 1
    await process_task(t, deps)
    assert [c.action for c in executor.calls] == [Action.MESSAGE]


# ── dead browser: crash, restart, never a false session expiry ───────────


async def test_page_did_not_load_is_a_crash_with_browser_restart(deps, executor, pool):
    lead, _ = await seed(deps, step="post.m1", branch="posts")
    executor.script(Action.MESSAGE, {"status": "failed", "error": "page_did_not_load"})
    t = await enqueue_step(deps, lead, "post.m1", template="m1")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.QUEUED and out.result.error_kind == "crash"
    assert pool.dead is True
    acct = await deps.accounts.get("default")
    assert acct.consecutive_failures == 0 and acct.session_expired_at is None


async def test_login_required_is_verified_before_flagging_session(deps, executor, pool):
    lead, _ = await seed(deps)
    executor.script(Action.VISIT, {"status": "failed", "error": "login_required"})
    pool.session_alive = True
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    assert pool.verifications == 1
    assert out.status == TaskStatus.QUEUED and not out.stop
    assert out.result.error_kind == "crash" and pool.dead is True
    assert (await deps.accounts.get("default")).session_expired_at is None


async def test_login_required_stands_when_feed_is_logged_out_or_unknown(deps, executor, pool):
    lead, _ = await seed(deps)
    executor.script(Action.VISIT, {"status": "failed", "error": "login_required"})
    for answer in (False, None):
        pool.session_alive = answer
        acct = await deps.accounts.get("default")
        acct.session_expired_at = None
        await deps.accounts.save(acct)
        t = await enqueue_step(deps, lead, "warm.visit")
        out = await process_task(t, deps)
        assert out.stop and (await deps.accounts.get("default")).session_expired_at == NOW


# ── stale replies from older conversations do not end a sequence ─────────


async def test_send_records_prior_reply_text(deps, executor):
    lead, _ = await seed(deps, step="post.m1", branch="posts")
    executor.script(Action.MESSAGE, {"status": "sent", "prior_reply_text": "Thanks Alex"})
    t = await enqueue_step(deps, lead, "post.m1", template="m1")
    await process_task(t, deps)
    lead2 = await deps.leads.get(lead.id)
    assert lead2.prior_reply_text == "Thanks Alex"
    assert lead2.last_message_text.startswith("Hi Jane")


async def test_scheduled_reply_check_ignores_old_history(deps, executor):
    lead, _ = await seed(deps, step="post.r1", branch="posts", prior_reply_text="Thanks Alex")
    executor.script(
        Action.CHECK_REPLIES,
        {"status": "replied", "reply_after_ours": False, "last_reply_text": "Thanks Alex"},
    )
    t = await enqueue_step(deps, lead, "post.r1")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.DONE and out.result.status == "none"
    lead2 = await deps.leads.get(lead.id)
    assert lead2.stage != LeadStage.REPLIED and lead2.replied_at is None
    assert (await deps.leads.get_sequence(lead.id)).step_id == "post.m2"


async def test_pre_send_check_ignores_old_history_and_sends(deps, executor):
    lead, _ = await seed(
        deps,
        step="post.m2",
        branch="posts",
        last_message_at=NOW - timedelta(days=3),
        last_message_text="Hi Jane, thanks for connecting.",
        prior_reply_text="Thanks Alex",
    )
    executor.script(Action.CHECK_REPLIES, {"status": "replied", "last_reply_text": "Thanks Alex"})
    t = await enqueue_step(deps, lead, "post.m2", template="m2")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.DONE and out.result.status == "sent"
    assert [c.action for c in executor.calls] == [Action.CHECK_REPLIES, Action.MESSAGE]


async def test_real_reply_after_ours_still_stops_the_sequence(deps, executor):
    lead, _ = await seed(deps, step="post.r1", branch="posts", prior_reply_text="Thanks Alex")
    executor.script(
        Action.CHECK_REPLIES,
        {"status": "replied", "reply_after_ours": True, "last_reply_text": "Sure, tell me more"},
    )
    t = await enqueue_step(deps, lead, "post.r1")
    await process_task(t, deps)
    assert (await deps.leads.get(lead.id)).stage == LeadStage.REPLIED


# ── a task with parameters that can never be phrased is skipped, not retried ──


async def test_invalid_post_url_is_a_soft_skip_not_a_retry(deps, executor):
    from linkedin_agent.models import PostRef

    # The model blanks a stored URL that is not a post, so a poisoned *task parameter* is
    # the case left to guard: one queued directly (enqueue_action, an older database).
    bad_url = "https://www.linkedin.com/in/johndoe/"
    bad = PostRef(url=bad_url, posted_days_ago=1, text="Hiring!")
    assert bad.url == ""
    lead, _ = await seed(deps, step="warm.like", branch="posts", posts=[bad])
    t = await enqueue_step(deps, lead, "warm.like", post_url=bad_url, post_text=bad.text)
    out = await process_task(t, deps)
    assert out.status == TaskStatus.SKIPPED and out.result.status == "no_content"
    assert "Invalid LinkedIn post URL" in out.result.error
    assert executor.calls == []  # the browser was never asked
    acct = await deps.accounts.get("default")
    assert acct.consecutive_failures == 0 and acct.tripped_until is None
    assert (await deps.leads.get_sequence(lead.id)).step_id == "warm.comment"


async def test_visit_result_with_profile_url_as_post_url_does_not_poison_like(deps, executor):
    lead, _ = await seed(deps, posts=[], profile={}, stage=LeadStage.NEW)
    executor.script(
        Action.VISIT,
        {"status": "ok", "posts": [{"url": lead.linkedin_url, "posted_days_ago": 1, "text": "Hi"}]},
    )
    t = await enqueue_step(deps, lead, "warm.visit")
    await process_task(t, deps)
    lead2 = await deps.leads.get(lead.id)
    assert lead2.posts[0].text == "Hi" and lead2.posts[0].url == ""
    # the like step now targets the post by text, which the prompt allows
    from linkedin_agent.core.tasks import build_prompt

    prompt = build_prompt(Action.LIKE_POST, lead.linkedin_url, {"post_url": "", "post_text": "Hi"})
    assert "Hi" in prompt


# ── sleep and wake ────────────────────────────────────────────────────────


async def test_crashes_do_not_consume_attempts_but_have_their_own_ceiling(deps, executor):
    from linkedin_agent.core.runner import MAX_CRASH_RETRIES

    lead, _ = await seed(deps)
    executor.script(Action.VISIT, RuntimeError("websocket closed"))
    t = await enqueue_step(deps, lead, "warm.visit")
    for i in range(1, MAX_CRASH_RETRIES + 1):
        out = await process_task(t, deps)
        assert out.status == TaskStatus.QUEUED, i
        t = await deps.queue.get(t.id)
        t.not_before = None
        await deps.queue.update(t)
        t = await deps.queue.claim(t.id, NOW)
        assert t.attempts == 1  # never grows: each crash gives the attempt back
    out = await process_task(t, deps)  # one over the ceiling
    assert out.status == TaskStatus.FAILED
    assert (await deps.leads.get_sequence(lead.id)).next_due_at is None  # stalled for `retry`
    acct = await deps.accounts.get("default")
    assert acct.tripped_until is None and acct.consecutive_failures == 0


async def test_plain_failures_still_use_three_attempts(deps, executor):
    lead, _ = await seed(deps)
    executor.script(Action.VISIT, {"status": "failed", "error": "element missing"})
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    assert "retry 1/3" in out.note and (await deps.queue.get(t.id)).attempts == 1


async def test_loop_detects_sleep_restarts_browser_and_waits_for_network(deps, executor, pool):
    wall = [1_000_000.0]
    probes: list[bool] = []
    answers = [False, False, True]  # network comes back on the third probe

    async def fake_sleep(s: float) -> None:
        # the first nap (pacing after the task) "lasts" two hours: the laptop slept
        wall[0] += 7200.0 if not probes else s

    async def probe() -> bool:
        ok = answers[len(probes)] if len(probes) < len(answers) else True
        probes.append(ok)
        return ok

    deps.sleep = fake_sleep
    deps.wall = lambda: wall[0]
    deps.network_ok = probe
    events: list[str] = []
    ticks = 0

    async def tick():
        nonlocal ticks
        ticks += 1

    lead, _ = await seed(deps)
    camp = deps.campaigns["test"]
    await deps.queue.enqueue(seqeng.build_task(camp.step("warm.visit"), lead, camp, "default", NOW))
    # one task, then the pacing nap "lasts" two hours, then max_tasks ends the loop
    n = await run_loop(deps, "default", on_event=events.append, tick=tick, max_tasks=1)
    assert n == 1
    assert any("asleep" in e for e in events) and any("network back" in e for e in events)
    assert probes == [False, False, True] and pool.dead is True


async def test_loop_without_sleep_does_not_trigger_wake_handling(deps, executor, pool):
    events: list[str] = []
    deps.network_ok = lambda: (_ for _ in ()).throw(AssertionError("must not probe"))  # type: ignore[assignment]
    await run_loop(deps, "default", once=True, on_event=events.append)
    assert not any("asleep" in e for e in events) and pool.dead is False


async def test_comment_already_posted_is_success_without_second_post(deps, executor):
    lead, _ = await seed(deps, step="warm.comment", branch="posts")
    executor.script(
        Action.COMMENT_POST,
        {"status": "already_commented", "post_url": lead.posts[0].url},
    )
    t = await enqueue_step(
        deps,
        lead,
        "warm.comment",
        post_url=lead.posts[0].url,
        post_text=lead.posts[0].text,
        text="Nice point.",
    )
    out = await process_task(t, deps)
    assert out.status == TaskStatus.DONE and out.result.status == "already_commented"
    lead2 = await deps.leads.get(lead.id)
    assert lead2.posts[0].commented is True and lead2.last_touch_at is None
    assert (await deps.leads.get_sequence(lead.id)).step_id == "invite.posts"


# ── a loop that is meant to run for weeks ─────────────────────────────────


async def test_loop_survives_a_tick_that_raises(deps, executor):
    lead, _ = await seed(deps)
    camp = deps.campaigns["test"]
    await deps.queue.enqueue(seqeng.build_task(camp.step("warm.visit"), lead, camp, "default", NOW))
    calls = 0

    async def flaky_tick():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database is locked")

    events: list[str] = []
    n = await run_loop(deps, "default", on_event=events.append, tick=flaky_tick, max_tasks=1)
    assert n == 1, "the task after the bad tick still ran"
    assert any("scheduler tick failed: RuntimeError: database is locked" in e for e in events)


async def test_loop_survives_an_iteration_that_raises(deps, executor, monkeypatch):
    import linkedin_agent.core.runner as runner_mod

    lead, _ = await seed(deps)
    camp = deps.campaigns["test"]
    for _ in range(2):
        await deps.queue.enqueue(
            seqeng.build_task(camp.step("warm.visit"), lead, camp, "default", NOW)
        )
    real = runner_mod.process_task
    blown = False

    async def flaky(task, d):
        nonlocal blown
        if not blown:
            blown = True
            raise ValueError("unexpected shape from the model")
        return await real(task, d)

    monkeypatch.setattr(runner_mod, "process_task", flaky)
    events: list[str] = []
    n = await run_loop(deps, "default", on_event=events.append, max_tasks=1)
    assert n == 1
    assert any("run loop iteration failed: ValueError" in e for e in events)
    # the task whose iteration blew up is still claimed by this live process; the next
    # tick's stale-running sweep leaves it alone, and it is requeued once we are gone
    depth = await deps.queue.depth("default")
    assert depth["done"] == 1 and depth["running"] == 1


async def test_loop_gives_up_after_too_many_consecutive_errors(deps):
    from linkedin_agent.core.runner import MAX_LOOP_ERRORS

    calls = 0

    async def always_raises():
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    events: list[str] = []
    with pytest.raises(RuntimeError, match="boom"):
        await run_loop(deps, "default", on_event=events.append, tick=always_raises)
    assert calls == MAX_LOOP_ERRORS
    assert any("consecutive errors; stopping" in e for e in events)


async def test_sleep_in_the_middle_of_a_task_is_noticed(deps, executor, pool):
    """The laptop lid closes while the browser is on a profile: no nap is running, so
    only the wall-vs-monotonic drift can tell. The browser is restarted and the network
    checked before the next task, exactly as after a nap."""
    wall = [1_000_000.0]
    deps.wall = lambda: wall[0]
    deps.mono = lambda: 5_000.0  # a clock that stands still through a suspend

    def visit_then_suspend(task):
        wall[0] += 3 * 3600  # three hours pass on the wall clock during the action
        return {"status": "ok", "headline": "VP"}

    executor.script(Action.VISIT, visit_then_suspend)
    probes = 0

    async def probe() -> bool:
        nonlocal probes
        probes += 1
        return True

    deps.network_ok = probe
    lead, _ = await seed(deps)
    camp = deps.campaigns["test"]
    await deps.queue.enqueue(seqeng.build_task(camp.step("warm.visit"), lead, camp, "default", NOW))
    events: list[str] = []
    n = await run_loop(deps, "default", on_event=events.append, max_tasks=1)
    assert n == 1
    assert any("asleep" in e for e in events) and pool.dead is True and probes == 1


async def test_loop_waits_through_session_expiry_and_resumes_after_login(deps, executor, pool):
    acct = await deps.accounts.get("default")
    acct.session_expired_at = NOW
    await deps.accounts.save(acct)
    lead, _ = await seed(deps)
    camp = deps.campaigns["test"]
    await deps.queue.enqueue(seqeng.build_task(camp.step("warm.visit"), lead, camp, "default", NOW))

    async def login_happens_elsewhere(_: float) -> None:
        a = await deps.accounts.get("default")
        a.session_expired_at = None
        a.logged_in_at = NOW
        await deps.accounts.save(a)

    deps.sleep = login_happens_elsewhere
    events: list[str] = []
    n = await run_loop(deps, "default", on_event=events.append, max_tasks=1)
    assert n == 1, "the loop carried on after the login instead of exiting"
    assert any("waiting for it" in e for e in events)
    assert any("login detected; resuming" in e for e in events)
    assert pool.dead is True, "the profile has new cookies: the browser must be reopened"


async def test_rate_limited_sequence_task_is_parked_inside_its_window(deps, executor):
    """Tomorrow 08:00 is not inside the send window; the parked task must wait for it."""
    from linkedin_agent.core.runner import next_local_day
    from linkedin_agent.core.timezone import UTC, schedule_in_window

    deps.settings.daily_connect_limit = 1
    await deps.log.record("default", Action.CONNECT, None, True, "sent", NOW)
    lead, _ = await seed(deps, step="invite.posts", branch="posts")
    t = await enqueue_step(deps, lead, "invite.posts", note_template="connection_note")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.QUEUED and out.note == "rate_limited"
    parked = await deps.queue.get(t.id)
    expected_open, expected_close = schedule_in_window("send", next_local_day(NOW, "UTC"), UTC)
    assert parked.not_before == expected_open and parked.not_after == expected_close
    assert executor.calls == []


# ── statuses the model invents ────────────────────────────────────────────


async def test_invented_success_status_counts_as_the_success_it_names(deps, executor):
    """Seen live: like_post returned "liked_but_url_not_found". The like happened; the
    sequence must move on rather than stall with the task marked done."""
    lead, _ = await seed(deps, step="warm.like", branch="posts")
    executor.script(Action.LIKE_POST, {"status": "liked_but_url_not_found"})
    t = await enqueue_step(deps, lead, "warm.like", post_url="", post_text=lead.posts[0].text)
    out = await process_task(t, deps)
    assert out.status == TaskStatus.DONE and out.result.status == "liked"
    assert out.result.data["reported_status"] == "liked_but_url_not_found"
    seq = await deps.leads.get_sequence(lead.id)
    assert seq.step_id == "warm.comment" and seq.next_due_at is not None


async def test_unknown_status_is_retried_not_silently_stalled(deps, executor):
    lead, _ = await seed(deps, step="warm.follow")
    executor.script(Action.FOLLOW, {"status": "clicked_something"})
    t = await enqueue_step(deps, lead, "warm.follow")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.QUEUED and "retry 1/3" in out.note
    assert "unknown status 'clicked_something'" in out.result.error
    seq = await deps.leads.get_sequence(lead.id)
    assert seq.step_id == "warm.follow", "still on the step, and the task is queued again"


def test_log_line_shows_why_a_task_did_not_succeed():
    from linkedin_agent.core.runner import Outcome, _format

    t = Task(
        action=Action.CONNECT,
        profile_url="https://www.linkedin.com/in/x/",
        account="a",
        params={"lead_name": "Grega Jerkic"},
    )
    failed = Outcome(
        status=TaskStatus.QUEUED,
        note="other; retry 1/3",
        result=TaskResult(status="failed", error="Connect button not found after scrolling"),
    )
    line = _format(t, failed)
    assert "failed · other; retry 1/3 — Connect button not found" in line
    done = Outcome(status=TaskStatus.DONE, result=TaskResult(status="sent", error="ignored"))
    assert "ignored" not in _format(t, done)


async def test_caps_and_status_follow_ramp_start_week(deps):
    from linkedin_agent import reporting
    from linkedin_agent.core.runner import caps_for

    acct = await deps.accounts.get("default")
    assert caps_for(deps, acct, Action.CONNECT, NOW)[0] == 5, "fresh account: week 1 = 25%"
    deps.settings.ramp_start_week = 3
    assert caps_for(deps, acct, Action.CONNECT, NOW)[0] == 12, "week 3 = 60%"
    assert (await reporting.account_health(deps, "default", NOW))["ramp_week"] == 3


async def test_running_out_of_steps_retries_but_does_not_count_toward_the_breaker(deps, executor):
    """Seen live: four of twelve visits ran out of steps in an hour. Two in a row twice.
    Three would have tripped the breaker for 48 hours over the model's step budget."""
    from linkedin_agent.core.prompts import unfinished_run

    class History:
        def is_done(self):
            return False

        def number_of_steps(self):
            return 12

        def errors(self):
            return []

        def final_result(self):
            return ""

    lead, _ = await seed(deps)
    executor.script(Action.VISIT, unfinished_run(History(), 12))
    for n in (1, 2, 3):
        t = await enqueue_step(deps, lead, "warm.visit")
        out = await process_task(t, deps)
        acct = await deps.accounts.get("default")
        assert acct.consecutive_failures == 0 and acct.tripped_until is None
        assert "ran out of steps" in out.note
    # attempts are still spent: after the third the task is failed, not retried for ever
    assert out.status in (TaskStatus.QUEUED, TaskStatus.FAILED)


def test_visit_has_room_for_a_long_profile():
    from linkedin_agent.adapters.browser_use_executor import MAX_STEPS

    assert MAX_STEPS[Action.VISIT] >= 12


# ── a profile that no longer exists ends the lead, and never trips the breaker ──


async def _visit_missing_twice(deps, executor, scripted, action=Action.VISIT, step="warm.visit"):
    """Run one profile step twice with the same scripted result; return both outcomes."""
    lead, _ = await seed(deps, step=step, branch="posts")
    executor.script(action, scripted)
    t = await enqueue_step(deps, lead, step)
    first = await process_task(t, deps)
    acct_after_first = await deps.accounts.get("default")
    t = await deps.queue.get(t.id)
    t.status, t.attempts = TaskStatus.RUNNING, 2
    await deps.queue.update(t)
    second = await process_task(t, deps)
    return lead, first, acct_after_first, second


async def test_missing_profile_is_confirmed_once_then_ends_the_lead(deps, executor):
    """Seen live: a dead profile burned all three retries and each counted toward the
    breaker. It is a permanent condition; one confirming look is enough."""
    acct = await deps.accounts.get("default")
    acct.consecutive_failures = 2  # one more generic failure would trip the breaker
    await deps.accounts.save(acct)

    lead, first, acct, second = await _visit_missing_twice(
        deps, executor, {"status": "profile_not_found", "error": None}
    )
    # first sighting: retried, not believed yet, not a breaker signal
    assert first.status == TaskStatus.QUEUED and "confirming" in first.note
    assert acct.consecutive_failures == 2 and acct.tripped_until is None
    # second sighting: the lead ends cleanly, the task is done, the counter resets
    assert second.status == TaskStatus.DONE and second.result.status == "profile_not_found"
    assert (await deps.leads.get(lead.id)).stage == LeadStage.CANNOT_CONTACT
    assert (await deps.leads.get_sequence(lead.id)).step_id is None
    acct = await deps.accounts.get("default")
    assert acct.consecutive_failures == 0 and acct.tripped_until is None


async def test_missing_profile_reported_as_a_failed_error_is_the_same_thing(deps, executor):
    """The prompt names profile_not_found; the model still answers failed + page_not_found."""
    lead, first, _, second = await _visit_missing_twice(
        deps, executor, {"status": "failed", "error": "page_not_found"}
    )
    assert first.status == TaskStatus.QUEUED and "confirming" in first.note
    assert second.status == TaskStatus.DONE and second.result.status == "profile_not_found"
    assert second.result.data["reported_error"] == "page_not_found"
    assert (await deps.leads.get(lead.id)).stage == LeadStage.CANNOT_CONTACT


async def test_a_false_missing_profile_sighting_recovers_on_the_second_look(deps, executor):
    """A half-loaded page reads as "not found"; the retry sees the real profile."""
    lead, _ = await seed(deps, stage=LeadStage.NEW)
    executor.script(Action.VISIT, {"status": "profile_not_found"})
    t = await enqueue_step(deps, lead, "warm.visit")
    first = await process_task(t, deps)
    assert first.status == TaskStatus.QUEUED
    executor.script(Action.VISIT, {"status": "ok", "full_name": "Jane Doe", "posts": []})
    t = await deps.queue.get(t.id)
    t.status, t.attempts = TaskStatus.RUNNING, 2
    await deps.queue.update(t)
    second = await process_task(t, deps)
    assert second.status == TaskStatus.DONE and second.result.status == "ok"
    assert (await deps.leads.get(lead.id)).stage == LeadStage.WARMING


async def test_a_profile_that_vanished_after_its_visit_ends_the_lead_at_the_follow(deps, executor):
    """Seen live: visited on Wednesday, gone by Monday. The follow answered
    failed/page_not_found three times and each counted toward the breaker."""
    acct = await deps.accounts.get("default")
    acct.consecutive_failures = 2
    await deps.accounts.save(acct)
    lead, first, acct, second = await _visit_missing_twice(
        deps,
        executor,
        {"status": "failed", "error": "page_not_found"},
        action=Action.FOLLOW,
        step="warm.follow",
    )
    assert first.status == TaskStatus.QUEUED and "confirming" in first.note
    assert acct.consecutive_failures == 2 and acct.tripped_until is None
    assert second.status == TaskStatus.DONE and second.result.status == "profile_not_found"
    assert (await deps.leads.get(lead.id)).stage == LeadStage.CANNOT_CONTACT
    assert (await deps.leads.get_sequence(lead.id)).step_id is None
    assert (await deps.accounts.get("default")).consecutive_failures == 0


async def test_a_connect_to_a_missing_profile_ends_the_lead_without_a_verify_probe(deps, executor):
    """The connect step gets the same treatment, and the read-only cannot_connect check
    is not run against a page that is not there."""
    lead, first, _, second = await _visit_missing_twice(
        deps,
        executor,
        {"status": "profile_not_found", "error": None},
        action=Action.CONNECT,
        step="invite.posts",
    )
    assert first.status == TaskStatus.QUEUED and "confirming" in first.note
    assert second.status == TaskStatus.DONE and second.result.status == "profile_not_found"
    assert (await deps.leads.get(lead.id)).stage == LeadStage.CANNOT_CONTACT
    assert not any(c.action == Action.CHECK_CONNECTION for c in executor.calls)


async def test_other_visit_failures_still_count_toward_the_breaker(deps, executor):
    """The exemption is for a missing profile only; a generic failure is unchanged."""
    lead, _ = await seed(deps)
    executor.script(Action.VISIT, {"status": "failed", "error": "max_steps_reached"})
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.QUEUED and "confirming" not in out.note
    assert (await deps.accounts.get("default")).consecutive_failures == 1


# ── a failed task keeps a picture of the page it failed on ───────────────


class _ShotBrowser:
    async def take_screenshot(self) -> bytes:
        return b"\x89PNG\r\n\x1a\nfake"


class _ShotPool(FakePool):
    async def get_browser(self, account):
        return _ShotBrowser()


async def test_a_failed_task_keeps_a_screenshot_of_the_page(deps, executor):
    """Two profiles ate eleven connect attempts and every log line was the model's
    one-phrase summary of a page nobody else saw."""
    deps.pool = _ShotPool()
    lead, _ = await seed(deps)
    executor.script(Action.VISIT, {"status": "failed", "error": "element missing"})
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    path = Path(out.result.data["screenshot"])
    assert path.parent == deps.settings.home / "failures"
    assert path.name.endswith(f"-visit-{t.id[:8]}.png")
    assert path.read_bytes().startswith(b"\x89PNG")
    assert f"screenshot {path}" in out.note
    # the saved path travels with the task so `log` and the database can show it
    assert (await deps.queue.get(t.id)).result["data"]["screenshot"] == str(path)


async def test_a_browser_without_screenshots_fails_exactly_as_before(deps, executor):
    lead, _ = await seed(deps)
    executor.script(Action.VISIT, {"status": "failed", "error": "element missing"})
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    assert "screenshot" not in out.result.data and "screenshot" not in out.note
    assert not (deps.settings.home / "failures").exists()


async def test_a_screenshot_that_fails_is_not_a_second_failure(deps, executor):
    class Broken:
        async def take_screenshot(self) -> bytes:
            raise RuntimeError("target closed")

    class BrokenPool(FakePool):
        async def get_browser(self, account):
            return Broken()

    deps.pool = BrokenPool()
    lead, _ = await seed(deps)
    executor.script(Action.VISIT, {"status": "failed", "error": "element missing"})
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.QUEUED and "screenshot" not in out.result.data


async def test_no_screenshot_on_a_crash_because_the_browser_is_gone(deps, executor):
    deps.pool = _ShotPool()
    lead, _ = await seed(deps)
    executor.script(Action.VISIT, RuntimeError("Target closed"))
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    assert out.result.error_kind.value == "crash" and "screenshot" not in out.result.data


async def test_a_visit_labelled_failed_but_fully_read_advances_the_lead(deps, executor):
    lead, _ = await seed(deps, posts=[], profile={}, stage=LeadStage.NEW)
    executor.script(
        Action.VISIT,
        {
            "status": "failed",
            "full_name": "Janet Doe",
            "headline": "Group CTO",
            "location": "Paris",
            "posts": [],
        },
    )
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.DONE and out.result.status == "ok"
    lead2 = await deps.leads.get(lead.id)
    assert lead2.stage == LeadStage.WARMING and lead2.profile["headline"] == "Group CTO"
    assert (await deps.accounts.get("default")).consecutive_failures == 0


async def test_a_bare_invite_routes_on_to_the_acceptance_check(deps, executor):
    lead, _ = await seed(deps, step="invite.posts", branch="posts")
    executor.script(Action.CONNECT, {"status": "sent_without_note"})
    t = await enqueue_step(deps, lead, "invite.posts", note_template="connection_note")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.DONE and out.result.status == "sent_without_note"
    assert (await deps.leads.get(lead.id)).stage == LeadStage.INVITED
    assert (await deps.leads.get_sequence(lead.id)).step_id == "wait.accept"


async def test_a_crash_closes_the_browser_instead_of_only_flagging_it(deps, executor):
    """A timed-out task is still inside the browser holding the profile; flagging it dead
    only means the next task opens another. Killing it is what makes the orphan unwind."""
    lead, _ = await seed(deps)
    executor.script(Action.VISIT, RuntimeError("Target closed"))
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    assert out.result.error_kind.value == "crash"
    assert deps.pool.dead and deps.pool.shutdowns == 1


async def test_a_browser_that_never_starts_is_a_crash_not_a_hang(deps, executor, monkeypatch):
    """Seen live: browser start and its first tab request are what wedged, before any
    task ran, so the executor's own budget never got a turn and the loop sat for hours."""
    import linkedin_agent.core.runner as rn

    class NeverStarts(FakePool):
        async def get_browser(self, account):
            await asyncio.sleep(3600)

    monkeypatch.setattr(rn, "BROWSER_START_TIMEOUT_S", 0.05)
    deps.pool = NeverStarts()
    lead, _ = await seed(deps)
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    assert out.result.error_kind.value == "crash"
    assert "timed out" in (out.result.error or "")
    assert out.status == TaskStatus.QUEUED  # the attempt is given back


async def test_tab_cleanup_that_hangs_does_not_hold_the_loop(deps, executor, monkeypatch):
    import linkedin_agent.core.runner as rn

    class SlowCleanup(FakePool):
        async def cleanup_pages(self):
            await asyncio.sleep(3600)

    monkeypatch.setattr(rn, "BROWSER_CLEANUP_TIMEOUT_S", 0.05)
    deps.pool = SlowCleanup()
    lead, _ = await seed(deps, posts=[], profile={}, stage=LeadStage.NEW)
    executor.script(Action.VISIT, {"status": "ok", "full_name": "Jane Doe", "posts": []})
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.DONE  # the task still counts; the browser is retired
    assert deps.pool.dead


async def test_pre_send_reply_check_browser_start_cannot_hang(deps, executor, monkeypatch):
    """Seen live: a message task sat 'running' for eleven hours with nothing in the log.

    The reply check before a send asks the pool for a browser itself, and that call was
    the one `get_browser` left unbounded when the others were capped."""
    import linkedin_agent.core.runner as rn

    class NeverStarts(FakePool):
        async def get_browser(self, account):
            await asyncio.sleep(3600)

    monkeypatch.setattr(rn, "BROWSER_START_TIMEOUT_S", 0.05)
    deps.pool = NeverStarts()
    lead, _ = await seed(
        deps,
        step="post.m2",
        branch="posts",
        last_message_at=NOW - timedelta(days=3),
        last_message_text="Hi Jane, thanks for connecting.",
    )
    t = await enqueue_step(deps, lead, "post.m2", template="m2")
    out = await asyncio.wait_for(process_task(t, deps), 5)
    assert out.result.error_kind.value == "crash"
    assert executor.calls == []  # no browser, so neither the probe nor the send happened


async def test_a_task_that_outlives_every_deadline_is_abandoned(deps, executor, monkeypatch):
    """The backstop: whatever unbounded await turns up inside process_task next, one task
    must not hold the single browser and starve every task queued behind it."""
    import linkedin_agent.core.runner as rn

    async def never_returns(task, deps_):
        await asyncio.sleep(3600)

    monkeypatch.setattr(rn, "TASK_WALL_CLOCK_S", 0.05)
    monkeypatch.setattr(rn, "process_task", never_returns)
    lead, _ = await seed(deps, posts=[], profile={}, stage=LeadStage.NEW)
    camp = deps.campaigns["test"]
    t = seqeng.build_task(camp.step("warm.visit"), lead, camp, "default", NOW, {})
    await deps.queue.enqueue(t)
    n = await asyncio.wait_for(run_loop(deps, "default", once=True), 5)
    assert n == 1
    row = await deps.queue.get(t.id)
    assert row.status == TaskStatus.FAILED and row.result["status"] == "stuck"
    assert deps.pool.dead  # the browser it was holding is retired, not reused


async def test_a_replacement_task_checks_the_thread_before_sending_again(deps, executor):
    """Seen live: the send went out, the task hung before recording it, the restart
    requeued the row and the scheduler expired it as `window_missed`. The fresh task for
    the same step is a new row, so `attempts` is 1 and the old retry guard never fired."""
    lead, _ = await seed(deps, step="post.m1", branch="posts", stage=LeadStage.CONNECTED)
    camp = deps.campaigns["test"]
    lost = seqeng.build_task(camp.step("post.m1"), lead, camp, "default", NOW, {"template": "m1"})
    await deps.queue.enqueue(lost)
    await deps.queue.claim(lost.id, NOW)
    await deps.queue.finish(lost.id, TaskResult(status="window_missed"), TaskStatus.SKIPPED)

    executor.script(Action.CHECK_REPLIES, {"status": "already_sent"})
    t = await enqueue_step(deps, lead, "post.m1", template="m1")
    assert t.attempts == 1
    out = await process_task(t, deps)
    assert out.status == TaskStatus.DONE and out.result.status == "sent"
    assert out.result.data["verified_by"] == "thread_check"
    assert [c.action for c in executor.calls] == [Action.CHECK_REPLIES]  # nothing re-sent


async def test_a_replacement_task_still_sends_when_the_thread_is_empty(deps, executor):
    """The same guard must not block a genuine send when the lost attempt never acted."""
    lead, _ = await seed(deps, step="post.m1", branch="posts", stage=LeadStage.CONNECTED)
    camp = deps.campaigns["test"]
    lost = seqeng.build_task(camp.step("post.m1"), lead, camp, "default", NOW, {"template": "m1"})
    await deps.queue.enqueue(lost)
    await deps.queue.claim(lost.id, NOW)
    await deps.queue.finish(lost.id, TaskResult(status="window_missed"), TaskStatus.SKIPPED)

    executor.script(Action.CHECK_REPLIES, {"status": "not_sent"})
    t = await enqueue_step(deps, lead, "post.m1", template="m1")
    out = await process_task(t, deps)
    assert out.status == TaskStatus.DONE and out.result.status == "sent"
    assert [c.action for c in executor.calls] == [Action.CHECK_REPLIES, Action.MESSAGE]


async def test_a_screenshot_that_hangs_does_not_hold_the_task(deps, executor, monkeypatch):
    """Three connect tasks were abandoned at the 45 minute ceiling. The screenshot added
    to diagnose connect failures takes the same event bus the failing task died in."""
    import linkedin_agent.core.runner as rn

    class HangingShot(FakePool):
        async def get_browser(self, account):
            browser = await super().get_browser(account)

            async def take_screenshot():
                await asyncio.sleep(3600)

            browser.take_screenshot = take_screenshot
            return browser

    monkeypatch.setattr(rn, "SCREENSHOT_TIMEOUT_S", 0.05)
    deps.pool = HangingShot()
    lead, _ = await seed(deps, step="invite.posts", branch="posts", stage=LeadStage.WARMING)
    executor.script(Action.CONNECT, {"status": "failed", "error": "note box never opened"})
    t = await enqueue_step(deps, lead, "invite.posts", note_template="connection_note")
    out = await asyncio.wait_for(process_task(t, deps), 5)
    assert out.status in (TaskStatus.QUEUED, TaskStatus.FAILED)  # the failure is recorded
    assert "screenshot" not in (out.result.data or {})


async def test_a_session_check_that_hangs_does_not_hold_the_task(deps, executor, monkeypatch):
    """The false-login_required check asks the browser a question. A browser wedged
    enough to fake a login page is wedged enough not to answer."""
    import linkedin_agent.core.runner as rn

    class SlowVerify(FakePool):
        async def verify_session(self, account):
            await asyncio.sleep(3600)

    monkeypatch.setattr(rn, "BROWSER_CLEANUP_TIMEOUT_S", 0.05)
    deps.pool = SlowVerify()
    lead, _ = await seed(deps, posts=[], profile={}, stage=LeadStage.NEW)
    executor.script(Action.VISIT, {"status": "failed", "error": "login_required"})
    t = await enqueue_step(deps, lead, "warm.visit")
    out = await asyncio.wait_for(process_task(t, deps), 5)
    assert out.stop is True  # unanswered means we believe the session is gone


async def test_a_grown_process_hands_over_between_tasks(deps, executor, monkeypatch):
    """The loop runs for weeks and the process grows while it does. Past the limit it
    closes the browser at the idle point and asks for a fresh process; never mid-task."""
    import linkedin_agent.core.runner as rn

    lead, _ = await seed(deps, posts=[], profile={}, stage=LeadStage.NEW)
    executor.script(Action.VISIT, {"status": "ok", "full_name": "Jane Doe", "posts": []})
    camp = deps.campaigns["test"]
    t = seqeng.build_task(camp.step("warm.visit"), lead, camp, "default", NOW, {})
    await deps.queue.enqueue(t)
    monkeypatch.setattr(rn, "process_rss_mb", lambda: deps.settings.max_rss_mb + 1)
    events: list[str] = []
    with pytest.raises(rn.RecycleRequested, match="MB"):
        await run_loop(deps, "default", max_tasks=5, on_event=events.append)
    # once, straight out: not logged as a loop error, not backed off and retried ten
    # times (seen live: twenty minutes of "recycling" lines before the process left)
    assert [e for e in events if "failed" in e] == []
    assert sum("recycling" in e for e in events) == 1
    # nothing was claimed: the check happens before a task is taken, not after
    assert (await deps.queue.get(t.id)).status == TaskStatus.QUEUED
    assert deps.pool.dead


async def test_once_mode_ignores_the_recycle_limit(deps, executor, monkeypatch):
    """`run --once` is a one-shot; it drains and exits on its own."""
    import linkedin_agent.core.runner as rn

    lead, _ = await seed(deps, posts=[], profile={}, stage=LeadStage.NEW)
    executor.script(Action.VISIT, {"status": "ok", "full_name": "Jane Doe", "posts": []})
    camp = deps.campaigns["test"]
    t = seqeng.build_task(camp.step("warm.visit"), lead, camp, "default", NOW, {})
    await deps.queue.enqueue(t)
    monkeypatch.setattr(rn, "process_rss_mb", lambda: deps.settings.max_rss_mb + 1)
    assert await run_loop(deps, "default", once=True) == 1
