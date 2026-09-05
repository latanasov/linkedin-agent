"""Map (action, result) to lead-state side effects. Pure."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models import TOUCH_ACTIONS, Action, LeadRecord, LeadStage, PostRef, TaskResult
from .prompts import LINKEDIN_COMPANY_URL_RE, LINKEDIN_POST_URL_RE
from .timezone import guess_timezone

# Result statuses per action that count as "the action did what it was asked".
SUCCESS_STATUSES: dict[Action, frozenset[str]] = {
    Action.VISIT: frozenset({"ok", "success"}),
    Action.FOLLOW: frozenset({"followed", "already_following"}),
    Action.LIKE_POST: frozenset({"liked", "already_liked"}),
    Action.COMMENT_POST: frozenset({"commented", "already_commented"}),
    Action.CONNECT: frozenset({"sent", "already_connected", "already_pending"}),
    Action.CHECK_CONNECTION: frozenset({"connected", "pending", "not_connected"}),
    Action.WITHDRAW_INVITE: frozenset({"withdrawn", "not_pending"}),
    Action.MESSAGE: frozenset({"sent"}),
    Action.INMAIL: frozenset({"sent"}),
    Action.CHECK_REPLIES: frozenset({"replied", "none", "no_thread"}),
}

# Result statuses that mean the prospect cannot be contacted on this path at all.
CANNOT_CONTACT_STATUSES: dict[Action, frozenset[str]] = {
    Action.MESSAGE: frozenset({"not_connected", "cannot_message"}),
    Action.INMAIL: frozenset({"cannot_message"}),
    Action.CONNECT: frozenset({"cannot_connect"}),
}

# Statuses that are neither success nor "cannot contact": the step is simply not applicable
# (post gone, nothing to withdraw). The sequence moves on without a retry.
SOFT_SKIP_STATUSES: dict[Action, frozenset[str]] = {
    Action.FOLLOW: frozenset({"cannot_follow"}),
    Action.LIKE_POST: frozenset({"post_not_found", "cannot_like"}),
    Action.COMMENT_POST: frozenset({"post_not_found", "cannot_comment"}),
    Action.WITHDRAW_INVITE: frozenset({"not_pending"}),
    Action.CHECK_REPLIES: frozenset({"no_thread"}),
}

_STAGE_RANK = {
    LeadStage.NEW: 0,
    LeadStage.WARMING: 1,
    LeadStage.INVITED: 2,
    LeadStage.CONNECTED: 3,
    LeadStage.MESSAGING: 4,
    LeadStage.REPLIED: 5,
    LeadStage.NURTURE: 5,
    LeadStage.NOT_ACCEPTED: 5,
    LeadStage.CANNOT_CONTACT: 5,
    LeadStage.DONE: 6,
    LeadStage.PAUSED: -1,
}


def is_success(action: Action, result: TaskResult) -> bool:
    return result.status in SUCCESS_STATUSES.get(action, frozenset())


def is_cannot_contact(action: Action, result: TaskResult) -> bool:
    return result.status in CANNOT_CONTACT_STATUSES.get(action, frozenset())


def is_soft_skip(action: Action, result: TaskResult) -> bool:
    return result.status in SOFT_SKIP_STATUSES.get(action, frozenset())


def _advance_stage(lead: LeadRecord, stage: LeadStage) -> None:
    """Move forward only; never regress a lead that is already further along."""
    if lead.stage == LeadStage.PAUSED:
        return
    if _STAGE_RANK[stage] >= _STAGE_RANK[lead.stage]:
        lead.stage = stage


_PROFILE_KEYS = (
    "full_name",
    "headline",
    "title",
    "company",
    "about",
    "location",
    "connection_degree",
    "company_page_url",
    "mutual_connections",
)


_TEXT_LIMITS = {"about": 2000, "headline": 300}
_DEFAULT_TEXT_LIMIT = 255


def clean_profile(data: dict[str, Any]) -> dict[str, Any]:
    """Whitelist and normalise what a visit reports about a profile.

    The browser model returns free text; nothing it says is trusted as-is. Strings are
    truncated, the degree is normalised to 1st/2nd/3rd, mutual_connections becomes an
    int, and company_page_url is kept only if it is a LinkedIn company/school link.
    Anything else is dropped rather than stored."""
    out: dict[str, Any] = {}
    for key in _PROFILE_KEYS:
        value = data.get(key)
        if value is None or value == "":
            continue
        if key == "mutual_connections":
            try:
                n = int(str(value).strip().replace(",", "").split()[0])
            except (TypeError, ValueError, IndexError):
                continue
            if n >= 0:
                out[key] = n
        elif key == "connection_degree":
            text = str(value).lower()
            for degree in ("1st", "2nd", "3rd"):
                if degree in text or text.strip() == degree[0]:
                    out[key] = degree
                    break
        elif key == "company_page_url":
            url = str(value).strip()
            if LINKEDIN_COMPANY_URL_RE.match(url):
                out[key] = url.split("?")[0]
        else:
            text = " ".join(str(value).split())
            if text:
                out[key] = text[: _TEXT_LIMITS.get(key, _DEFAULT_TEXT_LIMIT)]
    return out


def clean_post_url(value: Any) -> str:
    """A post URL from any result: kept only when it is really a LinkedIn post link."""
    url = str(value or "").strip()
    return url if LINKEDIN_POST_URL_RE.match(url) else ""


def _parse_posts(raw: Any) -> list[PostRef]:
    posts: list[PostRef] = []
    if not isinstance(raw, list):
        return posts
    for item in raw[:5]:
        if isinstance(item, str):
            posts.append(PostRef(text=item[:300]))
        elif isinstance(item, dict):
            days = item.get("posted_days_ago")
            try:
                days_int = int(days) if days is not None and str(days).strip() != "" else None
            except (TypeError, ValueError):
                days_int = None
            # The model sometimes hands back the profile URL or a job listing as a post's
            # "url". Keep the text (the like/comment prompts can find the post by it) but
            # never store a link that validate_post_url would reject later.
            posts.append(
                PostRef(
                    url=clean_post_url(item.get("url")),
                    posted_days_ago=days_int,
                    text=str(item.get("text") or item.get("snippet") or item.get("title") or "")[
                        :300
                    ],
                )
            )
    return posts


def apply_visit(lead: LeadRecord, data: dict[str, Any], now: datetime, default_tz: str) -> None:
    clean = clean_profile(data)
    lead.profile = {**lead.profile, **clean}
    if clean.get("title") and not lead.title:
        lead.title = str(clean["title"])[:255]
    if clean.get("company") and not lead.company:
        lead.company = str(clean["company"])[:255]
    if clean.get("location") and not lead.location:
        lead.location = str(clean["location"])[:255]
    if clean.get("full_name") and not lead.first_name:
        lead.first_name = str(clean["full_name"]).split(" ")[0][:100]
    new_posts = _parse_posts(data.get("posts") or data.get("recent_posts"))
    if new_posts:
        # keep liked/commented flags for posts we already know by url
        known = {p.url: p for p in lead.posts if p.url}
        for p in new_posts:
            if p.url in known:
                p.liked, p.commented = known[p.url].liked, known[p.url].commented
        lead.posts = new_posts
    # Refine the time zone when we only had the campaign default and now know a location.
    if clean.get("location") and (not lead.timezone or lead.timezone == default_tz):
        lead.timezone = guess_timezone(str(clean["location"]), default_tz)
    elif not lead.timezone:
        lead.timezone = guess_timezone(lead.location, default_tz)


def apply_result(
    lead: LeadRecord, action: Action, result: TaskResult, now: datetime, default_tz: str = "UTC"
) -> LeadRecord:
    """Return a copy of `lead` with stage/timestamps/profile updated for this result."""
    lead = lead.model_copy(deep=True)
    status = result.status

    if (
        action in TOUCH_ACTIONS
        and is_success(action, result)
        and status
        not in (
            "already_following",
            "already_liked",
            "already_commented",
            "already_pending",
        )
    ):
        lead.last_touch_at = now

    if action == Action.VISIT and is_success(action, result):
        apply_visit(lead, result.data, now, default_tz)
        _advance_stage(lead, LeadStage.WARMING)
    elif action == Action.LIKE_POST and status == "liked":
        _mark_post(lead, clean_post_url(result.data.get("post_url")), liked=True)
    elif action == Action.COMMENT_POST and status in ("commented", "already_commented"):
        _mark_post(lead, clean_post_url(result.data.get("post_url")), commented=True)
    elif action == Action.CONNECT:
        if status == "sent":
            lead.invited_at = lead.invited_at or now
            _advance_stage(lead, LeadStage.INVITED)
        elif status == "already_pending":
            lead.invited_at = lead.invited_at or now
            _advance_stage(lead, LeadStage.INVITED)
        elif status == "already_connected":
            lead.connected_at = lead.connected_at or now
            _advance_stage(lead, LeadStage.CONNECTED)
    elif action == Action.CHECK_CONNECTION and status == "connected":
        lead.connected_at = lead.connected_at or now
        _advance_stage(lead, LeadStage.CONNECTED)
    elif action == Action.WITHDRAW_INVITE and status == "withdrawn":
        _advance_stage(lead, LeadStage.NOT_ACCEPTED)
    elif action in (Action.MESSAGE, Action.INMAIL) and status == "sent":
        lead.last_message_at = now
        _advance_stage(lead, LeadStage.MESSAGING)
    elif action == Action.CHECK_REPLIES and status == "replied":
        lead.replied_at = lead.replied_at or now
        _advance_stage(lead, LeadStage.REPLIED)

    if is_cannot_contact(action, result):
        _advance_stage(lead, LeadStage.CANNOT_CONTACT)

    return lead


def _mark_post(lead: LeadRecord, url: Any, *, liked: bool = False, commented: bool = False) -> None:
    for p in lead.posts:
        if url and p.url == url:
            p.liked = p.liked or liked
            p.commented = p.commented or commented
            return
    # Unknown url: mark the newest post, which is what pick=newest targeted.
    if lead.posts:
        lead.posts[0].liked = lead.posts[0].liked or liked
        lead.posts[0].commented = lead.posts[0].commented or commented


def _norm_text(text: Any) -> str:
    return " ".join(str(text or "").split()).strip().lower()


def normalize_reply_check(result: TaskResult, lead: LeadRecord | None) -> TaskResult:
    """Downgrade a 'replied' verdict that is really old history.

    Two independent signals, either one is enough:
    - the model itself says the reply sits above our message (reply_after_ours false);
    - the quoted reply is the same text that already existed when we last sent
      (lead.prior_reply_text), so it cannot be an answer to that send.
    """
    if result.status != "replied":
        return result
    data = result.data or {}
    quoted = _norm_text(data.get("last_reply_text"))
    stale_by_position = data.get("reply_after_ours") is False
    known = _norm_text(lead.prior_reply_text) if lead and lead.prior_reply_text else ""
    stale_by_text = bool(quoted and known) and (
        quoted[:80] == known[:80] or quoted.startswith(known[:60]) or known.startswith(quoted[:60])
    )
    if stale_by_position or stale_by_text:
        reason = "position" if stale_by_position else "matches prior_reply_text"
        return TaskResult(status="none", data={**data, "stale_reply_ignored": reason})
    return result
