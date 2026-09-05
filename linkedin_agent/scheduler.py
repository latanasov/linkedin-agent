"""Materialise due sequence steps into tasks, respecting windows, spacing and caps."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .core import messages as msg
from .core import sequence as seqeng
from .core.limits import governor_state, remaining, spacing_ok
from .core.runner import Deps, caps_for, usage
from .models import (
    TOUCH_ACTIONS,
    AccountState,
    Action,
    Campaign,
    LeadRecord,
    LeadSequence,
    LeadStage,
    SequenceStep,
    TaskResult,
    TaskStatus,
)

logger = logging.getLogger(__name__)

STALE_RUNNING_S = 30 * 60
GOVERNOR_LOOKBACK_START = timedelta(days=21)
GOVERNOR_LOOKBACK_END = timedelta(days=3)


@dataclass
class TickReport:
    materialized: int = 0
    skipped_steps: int = 0
    deferred: int = 0
    expired: int = 0
    requeued: int = 0
    reviews: int = 0
    governor: str | None = None
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.materialized} tasks scheduled"]
        if self.reviews:
            parts.append(f"{self.reviews} awaiting review")
        if self.deferred:
            parts.append(f"{self.deferred} deferred (caps/spacing)")
        if self.expired:
            parts.append(f"{self.expired} expired (window missed)")
        if self.requeued:
            parts.append(f"{self.requeued} requeued")
        if self.governor:
            parts.append(f"governor → {self.governor}")
        return " · ".join(parts)


async def update_governor(deps: Deps, account: str, now: datetime) -> str | None:
    acct = await deps.accounts.get(account)
    if acct.governor_checked_at and now - acct.governor_checked_at < timedelta(days=1):
        return None
    invited, accepted = await deps.leads.acceptance_sample(
        now - GOVERNOR_LOOKBACK_START, now - GOVERNOR_LOOKBACK_END
    )
    rate = (accepted / invited) if invited else None
    new_state = governor_state(acct.governor_state, rate, invited)
    changed = new_state != acct.governor_state
    acct.governor_state = new_state
    acct.governor_checked_at = now
    await deps.accounts.save(acct)
    if changed:
        pct = f"{rate:.0%}" if rate is not None else "n/a"
        return f"{new_state.value} (acceptance {pct} over {invited} invites)"
    return None


async def tick(deps: Deps, account: str, now: datetime | None = None) -> TickReport:
    now = now or deps.clock()
    report = TickReport()
    report.expired = await deps.queue.expire_overdue(now)
    report.requeued = await deps.queue.requeue_stale_running(now, STALE_RUNNING_S)
    report.governor = await update_governor(deps, account, now)

    acct = await deps.accounts.get(account)
    if acct.session_expired_at or (acct.tripped_until and acct.tripped_until > now):
        report.notes.append("account gated (session expired or breaker tripped); nothing scheduled")
        return report

    allowance = Allowance(deps, account, acct, now)
    report.notes.extend(refresh_campaigns(deps))

    for lead, seq in await deps.leads.due_sequences(now):
        campaign = deps.campaigns.get(seq.campaign)
        if campaign is None:
            report.notes.append(f"{lead.display_name}: campaign {seq.campaign!r} not loaded")
            continue
        try:
            outcome = await _schedule_lead(
                deps, account, lead, seq, campaign, now, allowance, report
            )
        except KeyError as e:
            # The step this lead sits on is gone: the campaign file was edited under it.
            report.notes.append(
                f"{lead.display_name}: step {e.args[0]!r} no longer exists in campaign "
                f"{campaign.name!r}; `linkedin-agent restart <lead> --step <id>` to move it"
            )
            continue
        except Exception as e:  # one lead must not take the tick, or the run, down
            logger.exception("Scheduling %s failed", lead.display_name)
            report.notes.append(f"{lead.display_name}: {type(e).__name__}: {str(e)[:120]}")
            continue
        if outcome == "materialized":
            report.materialized += 1
        elif outcome == "deferred":
            report.deferred += 1
    return report


def refresh_campaigns(deps: Deps) -> list[str]:
    """Pick up edited campaign files without a restart.

    The run loop is meant to stay up for weeks, and people edit messages and delays while
    it runs, often through an assistant. Every tick compares the campaign files' mtimes
    with what was loaded; on a change the folder is reloaded and the entries that came
    from disk are replaced. A file that no longer validates keeps its last good version,
    with a note, so a half-finished edit cannot stall the people already in it."""
    from .campaigns import CampaignError, load_campaign

    folder = deps.settings.campaigns_dir
    seen: dict[str, float] = {}
    if folder.exists():
        for path in sorted(folder.glob("*.y*ml")):
            try:
                seen[str(path)] = path.stat().st_mtime
            except OSError:
                continue
    if deps.campaign_files is None:  # first tick: remember what build_app loaded
        deps.campaign_files = seen
        return []
    if seen == deps.campaign_files:
        return []
    notes: list[str] = []
    for path_s, mtime in seen.items():
        if deps.campaign_files.get(path_s) == mtime:
            continue
        try:
            c = load_campaign(Path(path_s))
        except CampaignError as e:
            notes.append(f"campaign file {Path(path_s).name} not reloaded: {str(e)[:160]}")
            continue
        deps.campaigns[c.name] = c
        notes.append(f"campaign {c.name!r} reloaded from {Path(path_s).name}")
    for path_s in set(deps.campaign_files) - set(seen):
        notes.append(
            f"campaign file {Path(path_s).name} was removed; its last loaded version stays "
            "in use until the run restarts"
        )
    deps.campaign_files = seen
    return notes


class Allowance:
    """How many more tasks of each action may be scheduled in this tick."""

    def __init__(self, deps: Deps, account: str, acct: AccountState, now: datetime) -> None:
        self._deps, self._account, self._acct, self._now = deps, account, acct, now
        self._left: dict[Action, int] = {}

    async def get(self, action: Action) -> int:
        if action not in self._left:
            day, week = await usage(self._deps, self._account, action, self._now)
            open_n = await self._deps.queue.count_open(self._account, action)
            caps = caps_for(self._deps, self._acct, action, self._now)
            self._left[action] = remaining(day + open_n, week + open_n, caps)
        return self._left[action]

    def take(self, action: Action) -> None:
        self._left[action] = max(0, self._left.get(action, 1) - 1)


async def _schedule_lead(
    deps: Deps,
    account: str,
    lead: LeadRecord,
    seq: LeadSequence,
    campaign: Campaign,
    now: datetime,
    allowance: Allowance,
    report: TickReport,
) -> str:
    # Walk past steps that do not apply (branch mismatch, nothing to like) — bounded.
    for _ in range(len(campaign.steps) + 1):
        if seq.step_id is None:
            return "ended"
        step = campaign.step(seq.step_id)
        if seq.branch is None and step.branch != "any":
            seq.branch = seqeng.decide_branch(lead, campaign)
        if not seqeng.step_applies(step, seq.branch):
            seq = await _skip(deps, lead, seq, campaign, now, report)
            continue
        params = await _params_for(step, lead, campaign)
        if params is None:  # e.g. no post to like/comment on
            seq = await _skip(deps, lead, seq, campaign, now, report)
            continue
        break
    else:
        return "ended"

    if await deps.queue.open_task_for(lead.id, step.id):
        return "open"
    if step.action in TOUCH_ACTIONS and not deps.settings.fast_test:
        t24 = await deps.log.touches(lead.id, now - timedelta(hours=24))
        t48 = await deps.log.touches(lead.id, now - timedelta(hours=48))
        if not spacing_ok(t24, t48):
            return "deferred"
    if await allowance.get(step.action) <= 0:
        return "deferred"

    params["lead_name"] = lead.display_name
    task = seqeng.build_task(
        step, lead, campaign, account, now, params, respect_windows=not deps.settings.fast_test
    )

    if step.action == Action.COMMENT_POST and campaign.review_comments:
        if deps.text_llm is None:
            report.notes.append(f"{lead.display_name}: cannot draft comment without an LLM")
            return "deferred"
        post = seqeng.pick_post(lead, str(step.params.get("pick", "newest")))
        assert post is not None
        draft, problems = await msg.draft_comment(
            post, lead, campaign, deps.text_llm, int(step.params.get("max_sentences", 3))
        )
        if draft is None:
            report.notes.append(
                f"{lead.display_name}: comment draft rejected ({'; '.join(problems)})"
            )
            await _skip(deps, lead, seq, campaign, now, report)
            return "skipped"
        task.status = TaskStatus.AWAITING_REVIEW
        await deps.queue.enqueue(task)
        await deps.review.submit(task.id, "comment", msg.context_for_review(post, lead), draft)
        report.reviews += 1
    else:
        await deps.queue.enqueue(task)
    allowance.take(step.action)
    return "materialized"


async def _params_for(
    step: SequenceStep, lead: LeadRecord, campaign: Campaign
) -> dict[str, object] | None:
    p: dict[str, object] = {
        k: v for k, v in step.params.items() if k not in ("repeat_every", "until_days")
    }
    if step.action in (Action.LIKE_POST, Action.COMMENT_POST):
        post = seqeng.pick_post(lead, str(step.params.get("pick", "newest")))
        if post is None:
            return None
        p["post_url"] = post.url
        p["post_text"] = post.text
    elif step.action == Action.CHECK_REPLIES:
        if lead.last_message_text:
            p["last_message_snippet"] = lead.last_message_text[:80]
    return p


async def _skip(
    deps: Deps,
    lead: LeadRecord,
    seq: LeadSequence,
    campaign: Campaign,
    now: datetime,
    report: TickReport,
) -> LeadSequence:
    adv = seqeng.skip_current(seq, campaign, now, deps.rng)
    await deps.leads.save_sequence(adv.seq)
    report.skipped_steps += 1
    if adv.ended_stage is not None and lead.stage != LeadStage.PAUSED:
        lead.stage = adv.ended_stage
        await deps.leads.update(lead)
    return adv.seq


# ── review decisions ──────────────────────────────────────────────────────


async def resolve_review(deps: Deps, task_id: str, approved_text: str | None, now: datetime) -> str:
    """Approve (with possibly edited text) or reject a drafted comment."""
    task = await deps.queue.get(task_id)
    if task is None:
        return "task not found"
    await deps.review.decide(task_id, approved_text, now)
    if approved_text is None:
        await deps.queue.finish(
            task_id, TaskResult(status="rejected_in_review"), TaskStatus.SKIPPED
        )
        lead = await deps.leads.get(task.lead_id) if task.lead_id else None
        seq = await deps.leads.get_sequence(task.lead_id) if task.lead_id else None
        campaign = deps.campaigns.get(lead.campaign) if lead else None
        if lead and seq and campaign and seq.step_id == task.step_id:
            adv = seqeng.skip_current(seq, campaign, now, deps.rng)
            await deps.leads.save_sequence(adv.seq)
        return "rejected; sequence moves on"
    problems = msg.check_comment(approved_text)
    if problems:
        return "not approved: " + "; ".join(problems)
    task.params["text"] = approved_text
    task.status = TaskStatus.QUEUED
    # re-window: the review may have happened days later
    lead = await deps.leads.get(task.lead_id) if task.lead_id else None
    campaign = deps.campaigns.get(lead.campaign) if lead else None
    if lead and campaign and task.step_id:
        fresh = seqeng.build_task(
            campaign.step(task.step_id),
            lead,
            campaign,
            task.account,
            now,
            task.params,
            respect_windows=not deps.settings.fast_test,
        )
        task.not_before, task.not_after = fresh.not_before, fresh.not_after
    await deps.queue.update(task)
    return "approved; queued"


async def retry_lead(deps: Deps, lead: LeadRecord, now: datetime) -> str:
    seq = await deps.leads.get_sequence(lead.id)
    if seq is None or seq.step_id is None:
        return "no active sequence"
    seq.next_due_at = now
    await deps.leads.save_sequence(seq)
    return f"re-armed step {seq.step_id}"


async def skip_lead_step(deps: Deps, lead: LeadRecord, now: datetime) -> str:
    seq = await deps.leads.get_sequence(lead.id)
    campaign = deps.campaigns.get(lead.campaign)
    if seq is None or seq.step_id is None or campaign is None:
        return "no active sequence"
    open_task = await deps.queue.open_task_for(lead.id, seq.step_id)
    if open_task:
        await deps.queue.finish(
            open_task.id, TaskResult(status="skipped_by_user"), TaskStatus.SKIPPED
        )
    adv = seqeng.skip_current(seq, campaign, now, deps.rng)
    await deps.leads.save_sequence(adv.seq)
    if adv.ended_stage is not None:
        lead.stage = adv.ended_stage
        await deps.leads.update(lead)
    return f"skipped {seq.step_id} → {adv.routed_to}"


# The stage a lead is put in when its sequence is restarted at a step with this action.
# Forward-only stage advancement means a lead stuck at a terminal stage must be lowered
# explicitly before the sequence can move it again.
STAGE_FOR_RESTART: dict[Action, LeadStage] = {
    Action.VISIT: LeadStage.WARMING,
    Action.FOLLOW: LeadStage.WARMING,
    Action.LIKE_POST: LeadStage.WARMING,
    Action.COMMENT_POST: LeadStage.WARMING,
    Action.CONNECT: LeadStage.WARMING,
    Action.CHECK_CONNECTION: LeadStage.INVITED,
    Action.WITHDRAW_INVITE: LeadStage.INVITED,
    Action.MESSAGE: LeadStage.CONNECTED,
    Action.INMAIL: LeadStage.CONNECTED,
    Action.CHECK_REPLIES: LeadStage.MESSAGING,
}


async def restart_lead(
    deps: Deps, lead: LeadRecord, now: datetime, step_id: str | None = None
) -> str:
    """Put a lead back into its sequence at `step_id` (default: the first step), whatever
    stage it ended in. Open tasks for the lead are cancelled; the step is due immediately."""
    campaign = deps.campaigns.get(lead.campaign)
    if campaign is None:
        return f"campaign {lead.campaign!r} not loaded"
    if step_id is None:
        step = seqeng.first_step(campaign)
    else:
        try:
            step = campaign.step(step_id)
        except KeyError:
            return f"unknown step {step_id!r}; steps: " + ", ".join(s.id for s in campaign.steps)
    first = step.id == seqeng.first_step(campaign).id

    cancelled = await deps.queue.cancel_open_for_leads([lead.id], "restarted")
    seq = await deps.leads.get_sequence(lead.id) or seqeng.new_sequence(lead, campaign, now)
    seq.history.append(
        {"step_id": step.id, "task_id": None, "result": "restarted", "at": now.isoformat()}
    )
    seq.campaign = campaign.name
    seq.step_id = step.id
    seq.step_entered_at = now
    seq.next_due_at = now
    if first:
        seq.branch = None  # re-decided after the visit
    await deps.leads.save_sequence(seq)

    lead.stage = LeadStage.NEW if first else STAGE_FOR_RESTART[step.action]
    await deps.leads.update(lead)
    return (
        f"restarted at {step.id} (stage {lead.stage.value}, {cancelled} queued task(s) cancelled)"
    )
