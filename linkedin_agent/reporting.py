"""Read-only views over the stores, shared by the CLI (`status`, `report`, `inbox`) and the
local web UI. Everything here returns plain dicts/lists so both front ends format them."""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from typing import Any

from .core.limits import ramp_week
from .core.runner import Deps, caps_for, usage
from .models import Action, LeadRecord, LeadSequence, LeadStage, Task

USAGE_ACTIONS: tuple[Action, ...] = (
    Action.VISIT,
    Action.FOLLOW,
    Action.LIKE_POST,
    Action.COMMENT_POST,
    Action.CONNECT,
    Action.MESSAGE,
    Action.INMAIL,
    Action.CHECK_CONNECTION,
    Action.CHECK_REPLIES,
)

ACCEPTANCE_BENCHMARK = 0.285
REPLY_BENCHMARK = 0.104


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


async def account_health(deps: Deps, account: str, now: datetime) -> dict[str, Any]:
    acct = await deps.accounts.get(account)
    profile_exists = deps.settings.profile_dir(account).exists()
    if acct.session_expired_at:
        login = "session_expired"
    elif acct.logged_in_at:
        login = "logged_in"
    elif profile_exists:
        login = "profile_exists"
    else:
        login = "not_logged_in"
    tripped = bool(acct.tripped_until and acct.tripped_until > now)
    age = (now - acct.first_action_at).days if acct.first_action_at else 0
    return {
        "account": account,
        "login": login,
        "logged_in_at": _iso(acct.logged_in_at),
        "session_expired_at": _iso(acct.session_expired_at),
        "breaker_tripped": tripped,
        "tripped_until": _iso(acct.tripped_until) if tripped else None,
        "trip_reason": acct.trip_reason if tripped else None,
        "consecutive_failures": acct.consecutive_failures,
        "governor": acct.governor_state.value,
        "ramp_week": ramp_week(age),
        "account_age_days": age,
        "models": models_in_use(deps.settings),
    }


def models_in_use(settings: Any) -> dict[str, str]:
    """Which model drives the browser and which writes text, and where each runs.

    Reported so a mix-up ("why is this so slow?") is visible without opening .env."""
    return {
        "browser": f"{settings.browser_llm_model} ({settings.browser_provider})",
        "text": f"{settings.text_llm_model} ({settings.text_provider})",
    }


async def usage_today(deps: Deps, account: str, now: datetime) -> list[dict[str, Any]]:
    acct = await deps.accounts.get(account)
    rows: list[dict[str, Any]] = []
    for action in USAGE_ACTIONS:
        day, week = await usage(deps, account, action, now)
        dcap, wcap = caps_for(deps, acct, action, now)
        rows.append(
            {"action": action.value, "day": day, "day_cap": dcap, "week": week, "week_cap": wcap}
        )
    return rows


async def queue_summary(deps: Deps, account: str) -> dict[str, Any]:
    depth = await deps.queue.depth(account)
    return {
        "queued": depth.get("queued", 0),
        "running": depth.get("running", 0),
        "awaiting_review": depth.get("awaiting_review", 0),
        "done": depth.get("done", 0),
        "failed": depth.get("failed", 0),
        "skipped": depth.get("skipped", 0),
        "review_pending": len(await deps.review.pending()),
        "inbox": len(await deps.leads.by_stage(LeadStage.REPLIED)),
    }


def task_row(t: Task) -> dict[str, Any]:
    result = t.result or {}
    return {
        "id": t.id,
        "lead_id": t.lead_id,
        "lead_name": t.params.get("lead_name") or t.profile_url,
        "profile_url": t.profile_url,
        "step_id": t.step_id,
        "action": t.action.value,
        "status": t.status.value,
        "attempts": t.attempts,
        "result_status": result.get("status"),
        "result_error": result.get("error"),
        "result_data": {k: v for k, v in result.items() if k not in ("status", "error")},
        "text": t.params.get("text") or t.params.get("note"),
        "not_before": _iso(t.not_before),
        "not_after": _iso(t.not_after),
        "created_at": _iso(t.created_at),
        "finished_at": _iso(t.finished_at),
    }


def lead_row(lead: LeadRecord, seq: LeadSequence | None) -> dict[str, Any]:
    return {
        "id": lead.id,
        "name": lead.display_name,
        "slug": lead.slug,
        "linkedin_url": lead.linkedin_url,
        "campaign": lead.campaign,
        "company": lead.company or lead.profile.get("company"),
        "title": lead.title or lead.profile.get("title"),
        "headline": lead.profile.get("headline"),
        "location": lead.location,
        "timezone": lead.timezone,
        "stage": lead.stage.value,
        "step_id": seq.step_id if seq else None,
        "branch": seq.branch if seq else None,
        "next_due_at": _iso(seq.next_due_at) if seq else None,
        "stalled": bool(seq and seq.step_id and seq.next_due_at is None),
        "posts": len([p for p in lead.posts if p.text]),
        "invited_at": _iso(lead.invited_at),
        "connected_at": _iso(lead.connected_at),
        "last_touch_at": _iso(lead.last_touch_at),
        "last_message_at": _iso(lead.last_message_at),
        "replied_at": _iso(lead.replied_at),
        "created_at": _iso(lead.created_at),
    }


async def lead_rows(deps: Deps, campaign: str | None = None) -> list[dict[str, Any]]:
    rows = []
    for lead in await deps.leads.all(campaign):
        seq = await deps.leads.get_sequence(lead.id)
        rows.append(lead_row(lead, seq))
    return rows


async def lead_detail(deps: Deps, lead: LeadRecord) -> dict[str, Any]:
    seq = await deps.leads.get_sequence(lead.id)
    row = lead_row(lead, seq)
    row.update(
        {
            "profile": lead.profile,
            "custom_fields": lead.custom_fields,
            "post_list": [p.model_dump() for p in lead.posts],
            "last_message_text": lead.last_message_text,
            "prior_reply_text": lead.prior_reply_text,
            "history": list(seq.history) if seq else [],
            "tasks": [task_row(t) for t in await deps.queue.for_lead(lead.id)],
        }
    )
    return row


async def inbox_rows(deps: Deps, now: datetime) -> list[dict[str, Any]]:
    leads = await deps.leads.by_stage(LeadStage.REPLIED)
    leads.sort(key=lambda r: r.replied_at or now, reverse=True)
    return [lead_row(ld, await deps.leads.get_sequence(ld.id)) for ld in leads]


async def campaign_report(
    deps: Deps, account: str, campaign: str | None, since: timedelta, now: datetime
) -> dict[str, Any]:
    start = now - since
    leads = await deps.leads.all(campaign)
    leads_in = [
        ld
        for ld in leads
        if (ld.created_at or now) >= start or (ld.invited_at and ld.invited_at >= start)
    ]
    invited = [ld for ld in leads_in if ld.invited_at]
    accepted = [ld for ld in invited if ld.connected_at]
    messaged = [ld for ld in leads_in if ld.last_message_at]
    replied = [ld for ld in leads_in if ld.replied_at]
    warmed = [ld for ld in leads_in if ld.stage not in (LeadStage.NEW, LeadStage.WARMING)]
    ttl = [
        (ld.connected_at - ld.invited_at).total_seconds() / 86400
        for ld in accepted
        if ld.invited_at and ld.connected_at
    ]
    acct = await deps.accounts.get(account)
    return {
        "campaign": campaign,
        "since_days": since.total_seconds() / 86400,
        "leads": len(leads_in),
        "warmed": len(warmed),
        "invited": len(invited),
        "accepted": len(accepted),
        "acceptance_rate": (len(accepted) / len(invited)) if invited else None,
        "acceptance_benchmark": ACCEPTANCE_BENCHMARK,
        "median_days_to_accept": statistics.median(ttl) if ttl else None,
        "withdrawn": await deps.log.count(account, Action.WITHDRAW_INVITE, start),
        "messaged": len(messaged),
        "replied": len(replied),
        "reply_rate": (len(replied) / len(messaged)) if messaged else None,
        "reply_benchmark": REPLY_BENCHMARK,
        "stages": await deps.leads.stage_counts(campaign),
        "governor": acct.governor_state.value,
        "breaker_tripped": bool(acct.tripped_until and acct.tripped_until > now),
        "rows": [lead_row(ld, None) for ld in leads_in],
    }
