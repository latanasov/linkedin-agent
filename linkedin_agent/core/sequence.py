"""The sequence engine: where a lead is in its campaign and what happens next. Pure."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from ..models import (
    Action,
    Campaign,
    LeadRecord,
    LeadSequence,
    LeadStage,
    PostRef,
    SequenceStep,
    Task,
    TaskResult,
)
from .status_map import is_cannot_contact, is_soft_skip, is_success
from .timezone import resolve_tz, schedule_in_window

JITTER_MAX_FRACTION = 0.4
BranchName = Literal["posts", "quiet"]


@dataclass
class Advance:
    seq: LeadSequence
    ended_stage: LeadStage | None = None
    routed_to: str | None = None


def decide_branch(lead: LeadRecord, campaign: Campaign) -> BranchName:
    """'posts' when the newest post is within quiet_threshold_days, else 'quiet'."""
    ages = [p.posted_days_ago for p in lead.posts if p.posted_days_ago is not None and p.text]
    if ages and min(ages) <= campaign.quiet_threshold_days:
        return "posts"
    # A post with text but unknown age still counts as recent activity.
    if any(p.text and p.posted_days_ago is None for p in lead.posts):
        return "posts"
    return "quiet"


def step_applies(step: SequenceStep, branch: BranchName | None) -> bool:
    return step.branch == "any" or branch is None or step.branch == branch


def next_step_in_order(
    campaign: Campaign, current_id: str, branch: BranchName | None
) -> SequenceStep | None:
    idx = campaign.step_index(current_id)
    for step in campaign.steps[idx + 1 :]:
        if step_applies(step, branch):
            return step
    return None


def first_step(campaign: Campaign) -> SequenceStep:
    return campaign.steps[0]


def jitter(delay: timedelta, rng: random.Random) -> timedelta:
    if delay <= timedelta(0):
        return timedelta(0)
    return delay + timedelta(seconds=delay.total_seconds() * rng.uniform(0, JITTER_MAX_FRACTION))


def new_sequence(lead: LeadRecord, campaign: Campaign, now: datetime) -> LeadSequence:
    return LeadSequence(
        lead_id=lead.id,
        campaign=campaign.name,
        step_id=first_step(campaign).id,
        branch=None,
        next_due_at=now,
        step_entered_at=now,
    )


def route(
    campaign: Campaign,
    step: SequenceStep,
    action: Action,
    result: TaskResult,
    branch: BranchName | None,
    step_entered_at: datetime | None,
    now: datetime,
) -> str | None:
    """Target step id, 'end:<stage>', the same step id (repeat), or None (stall)."""
    # Timeout on a repeating step wins over its ordinary routing.
    if step.until_days is not None and step_entered_at is not None:
        if (now - step_entered_at) >= timedelta(
            days=step.until_days
        ) and "timeout" in step.on_result:
            return step.on_result["timeout"]
    target = step.on_result.get(result.status)
    if target is not None:
        return target
    if is_cannot_contact(action, result):
        return f"end:{LeadStage.CANNOT_CONTACT.value}"
    if is_success(action, result) or is_soft_skip(action, result):
        nxt = next_step_in_order(campaign, step.id, branch)
        return nxt.id if nxt else f"end:{LeadStage.DONE.value}"
    return None  # hard failure: stall on this step until retried or skipped


def advance(
    seq: LeadSequence,
    campaign: Campaign,
    step: SequenceStep,
    result: TaskResult,
    now: datetime,
    rng: random.Random,
    task_id: str | None = None,
) -> Advance:
    seq = seq.model_copy(deep=True)
    seq.history.append(
        {"step_id": step.id, "task_id": task_id, "result": result.status, "at": now.isoformat()}
    )
    target = route(campaign, step, step.action, result, seq.branch, seq.step_entered_at, now)
    if target is None:
        seq.next_due_at = None  # stalled; `retry` re-arms it
        return Advance(seq=seq, routed_to=None)
    if target.startswith("end:"):
        seq.step_id = None
        seq.next_due_at = None
        return Advance(seq=seq, ended_stage=LeadStage(target[4:]), routed_to=target)
    if target == step.id:
        # repeat the same step; keep step_entered_at so until_days keeps counting
        every = step.repeat_every or step.delay or timedelta(days=1)
        seq.next_due_at = now + jitter(every, rng)
        return Advance(seq=seq, routed_to=target)
    nxt = campaign.step(target)
    seq.step_id = nxt.id
    seq.step_entered_at = now
    seq.next_due_at = now + jitter(nxt.delay, rng)
    return Advance(seq=seq, routed_to=target)


def skip_current(
    seq: LeadSequence, campaign: Campaign, now: datetime, rng: random.Random
) -> Advance:
    """Skip the current step without executing it (branch mismatch, no post, user skip)."""
    assert seq.step_id is not None
    step = campaign.step(seq.step_id)
    synthetic = TaskResult(status="skipped")
    seq2 = seq.model_copy(deep=True)
    seq2.history.append(
        {"step_id": step.id, "task_id": None, "result": "skipped", "at": now.isoformat()}
    )
    nxt = next_step_in_order(campaign, step.id, seq2.branch)
    if nxt is None:
        seq2.step_id = None
        seq2.next_due_at = None
        return Advance(
            seq=seq2, ended_stage=LeadStage.DONE, routed_to=f"end:{LeadStage.DONE.value}"
        )
    seq2.step_id = nxt.id
    seq2.step_entered_at = now
    seq2.next_due_at = now + jitter(nxt.delay, rng)
    del synthetic
    return Advance(seq=seq2, routed_to=nxt.id)


def pick_post(lead: LeadRecord, pick: str) -> PostRef | None:
    """Choose which post a like/comment step targets."""
    posts = [p for p in lead.posts if p.text]
    if not posts:
        return None
    posts.sort(key=lambda p: p.posted_days_ago if p.posted_days_ago is not None else 10_000)
    if pick == "different_from_liked":
        for p in posts:
            if not p.liked and not p.commented:
                return p
        for p in posts:
            if not p.commented:
                return p
        return None
    return posts[0]


def build_task(
    step: SequenceStep,
    lead: LeadRecord,
    campaign: Campaign,
    account: str,
    now: datetime,
    params: dict[str, object] | None = None,
    respect_windows: bool = True,
) -> Task:
    """Materialise a step into a Task with not_before/not_after from its window."""
    if respect_windows:
        tz = resolve_tz(lead.timezone, campaign.default_timezone)
        not_before, not_after = schedule_in_window(
            step.window, now, tz, campaign.window_specs or None
        )
    else:
        not_before, not_after = now, None
    return Task(
        lead_id=lead.id,
        step_id=step.id,
        action=step.action,
        profile_url=lead.linkedin_url,
        account=account,
        params=dict(params or {}),
        not_before=not_before,
        not_after=not_after,
        created_at=now,
    )
