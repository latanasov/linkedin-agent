"""Text: user-written message templates (rendered per lead) and model-written comments.

Messages, notes and InMails are the user's templates with merge fields plus at most one
model-filled `{hook}` sentence. Comments are drafted by the model from the post text.
Every rule the model must follow is also checked in code here.
"""

from __future__ import annotations

import hashlib
import re
import string
from dataclasses import dataclass, field
from typing import Any

from ..models import Campaign, LeadRecord, PostRef
from ..ports import TextLLM
from .prompts import sanitize_user_text

HOOK_FIELD = "hook"
HOOK_MAX_CHARS = 120

# Rendered-length guidance (warnings, not errors: the text is the user's).
LENGTH_LIMITS: dict[str, int] = {
    "connection_note": 150,
    "connection_note_quiet": 150,
    "m1": 300,
    "m2": 250,
    "m3": 220,
    "m4": 200,
    "inmail_subject": 60,
    "inmail_body": 400,
}

MERGE_DEFAULTS: dict[str, str] = {
    "first_name": "there",
    "last_name": "",
    "company": "your company",
    "title": "",
    "headline": "",
    "post_topic": "your recent post",
    "agent_name": "",
    "company_name": "",
    "booking_link": "",
    "location": "",
}

BANNED_COMMENT_PHRASES = (
    "great post",
    "so true",
    "thanks for sharing",
    "well said",
    "love this",
    "totally agree",
    "couldn't agree more",
    "could not agree more",
    "insightful",
    "check out",
    "dm me",
    "let's connect",
    "book a call",
    "our product",
    "our platform",
    "our solution",
)

_LINK_RE = re.compile(r"(https?://|www\.|\b[a-z0-9-]+\.(com|io|ai|co|net|org)\b)", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]+|[^.!?]+$")


class _Fields(dict[str, str]):
    """dict that records which keys fell back to a default."""

    def __init__(self, values: dict[str, str]) -> None:
        super().__init__(values)
        self.fallbacks: list[str] = []

    def __missing__(self, key: str) -> str:
        self.fallbacks.append(key)
        return MERGE_DEFAULTS.get(key, "")


@dataclass
class Rendered:
    text: str
    warnings: list[str] = field(default_factory=list)
    hook_used: bool = False
    hook_fallback_used: bool = False


def template_fields(template: str) -> set[str]:
    names: set[str] = set()
    for _, name, _, _ in string.Formatter().parse(template):
        if name:
            names.add(name.split(".")[0].split("[")[0])
    return names


def topic_from_post(text: str, max_chars: int = 60) -> str:
    """A short, deterministic topic phrase from a post's text for {post_topic}."""
    if not text:
        return ""
    first = re.split(r"(?<=[.!?])\s+|\n", text.strip(), maxsplit=1)[0]
    first = re.sub(r"#\w+", "", first)
    first = re.sub(r"\s+", " ", first).strip(" -—:;,")
    if len(first) > max_chars:
        cut = first[:max_chars].rsplit(" ", 1)[0]
        first = cut.rstrip(" ,;:-") + "…"
    return first


def newest_post(lead: LeadRecord) -> PostRef | None:
    with_text = [p for p in lead.posts if p.text]
    if not with_text:
        return None
    return min(
        with_text,
        key=lambda p: p.posted_days_ago if p.posted_days_ago is not None else 10_000,
    )


def fields_for(lead: LeadRecord, campaign: Campaign) -> dict[str, str]:
    post = newest_post(lead)
    fields: dict[str, str] = {
        "first_name": lead.first_name or "",
        "last_name": lead.last_name or "",
        "company": lead.company or str(lead.profile.get("company") or ""),
        "title": lead.title or str(lead.profile.get("title") or ""),
        "headline": str(lead.profile.get("headline") or ""),
        "location": lead.location or "",
        "post_topic": topic_from_post(post.text) if post else "",
        "agent_name": campaign.agent_name,
        "company_name": campaign.company_name,
        "booking_link": campaign.booking_link,
    }
    for k, v in lead.custom_fields.items():
        if v is None:
            continue
        sval = str(v)
        key = k.lower().strip()
        if key.startswith("custom_"):
            fields[key] = sval
            fields[key[len("custom_") :]] = sval
        else:
            fields[key] = sval
            fields[f"custom_{key}"] = sval
    return fields


def allowed_fields(campaign: Campaign, sample_custom: set[str] | None = None) -> set[str]:
    base = set(MERGE_DEFAULTS) | {HOOK_FIELD}
    if sample_custom:
        for k in sample_custom:
            key = k.lower().strip()
            base.add(key)
            if key.startswith("custom_"):
                base.add(key[len("custom_") :])
            else:
                base.add(f"custom_{key}")
    return base


_LONE_PLACEHOLDER_RE = re.compile(r"^\s*\{(\w+)\}\s*$")


def render(template: str, fields: dict[str, str]) -> Rendered:
    """Substitute merge fields. Unknown/empty fields fall back to MERGE_DEFAULTS.

    A line that consists of a single placeholder which renders empty (typically {hook},
    or {booking_link} when none is set) is dropped entirely rather than left blank."""
    values = {k: v for k, v in fields.items() if v}
    kept: list[str] = []
    for line in template.split("\n"):
        m = _LONE_PLACEHOLDER_RE.match(line)
        if m and not (values.get(m.group(1)) or MERGE_DEFAULTS.get(m.group(1), "")):
            continue
        kept.append(line)
    f = _Fields(values)
    text = string.Formatter().vformat("\n".join(kept), (), f)
    text = _tidy(text)
    warnings = [
        f"{k} was empty; used default" for k in dict.fromkeys(f.fallbacks) if k != HOOK_FIELD
    ]
    return Rendered(text=text, warnings=warnings)


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def body_hash(text: str) -> str:
    norm = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


# ── checks ────────────────────────────────────────────────────────────────


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.findall(text.replace("\n", " ")) if s.strip()]


def check_comment(text: str, max_sentences: int = 3, campaign: Campaign | None = None) -> list[str]:
    problems: list[str] = []
    t = (text or "").strip()
    if not t:
        return ["empty"]
    low = t.lower()
    for phrase in BANNED_COMMENT_PHRASES:
        if phrase in low:
            problems.append(f"banned phrase: {phrase!r}")
    if _LINK_RE.search(t):
        problems.append("contains a link")
    n = len(sentences(t))
    if n > max_sentences:
        problems.append(f"{n} sentences (max {max_sentences})")
    if len(t) > 600:
        problems.append("longer than 600 characters")
    if campaign:
        for name in (campaign.company_name, campaign.booking_link):
            if name and name.lower() in low:
                problems.append("mentions the sender's company or link")
    return problems


def check_hook(text: str) -> list[str]:
    problems: list[str] = []
    t = (text or "").strip()
    if not t:
        return ["empty"]
    if len(t) > HOOK_MAX_CHARS:
        problems.append(f"longer than {HOOK_MAX_CHARS} characters")
    if len(sentences(t)) > 1:
        problems.append("more than one sentence")
    if _LINK_RE.search(t):
        problems.append("contains a link")
    if "?" in t:
        problems.append("is a question")
    low = t.lower()
    for phrase in ("i came across your profile", "hope this finds you", "i noticed you"):
        if phrase in low:
            problems.append(f"cliché: {phrase!r}")
    return problems


# ── prompts ───────────────────────────────────────────────────────────────

HOOK_PROMPT = """You write ONE short sentence that opens a LinkedIn message from {agent_name}
to {first_name}.
The sentence must refer to something specific the recipient wrote or does, using the data below.

Recipient:
- Name: {first_name}
- Title: {title}
- Company: {company}
- Headline: {headline}
{post_block}

Rules:
- Exactly one sentence, at most {max_chars} characters.
- Refer to a concrete detail (their post, their headline, their role). No generic praise.
- Do NOT ask a question. Do NOT include links, product names, or a greeting.
- Do NOT start with "I came across your profile" or "I noticed you".
- Plain text only. Output the sentence and nothing else."""

COMMENT_PROMPT = """You write a LinkedIn comment on the post below, as {agent_name} ({title_line}).

Post by {first_name} ({headline}):
\"\"\"
{post_text}
\"\"\"

Rules:
- 1 to {max_sentences} sentences, under 400 characters.
- Add something the post did not already say: a specific observation, a related data point,
  a counter-example, or a precise question that shows you read it.
- Refer to a concrete detail from the post.
- No links. No product, company or tool names. No pitch. No "great post", "so true",
  "thanks for sharing" or similar praise-only openers. No hashtags. No emojis.
- Plain text only. Output the comment and nothing else."""


def _post_block(lead: LeadRecord) -> str:
    post = newest_post(lead)
    lines: list[str] = []
    if post:
        age = f" ({post.posted_days_ago} days ago)" if post.posted_days_ago is not None else ""
        lines.append(f"- Recent post{age}: {post.text[:300]}")
    about = lead.profile.get("about")
    if about:
        lines.append(f"- About: {str(about)[:200]}")
    return "\n".join(lines) if lines else "- (no posts or about text available)"


def has_personal_data(lead: LeadRecord) -> bool:
    return bool(newest_post(lead) or lead.profile.get("headline") or lead.profile.get("about"))


def _clean_llm_text(text: str) -> str:
    t = text.strip().strip('"').strip("'").strip()
    t = re.sub(r"^(comment|hook|sentence)\s*:\s*", "", t, flags=re.IGNORECASE)
    return t.strip()


async def draft_hook(lead: LeadRecord, campaign: Campaign, llm: TextLLM) -> tuple[str, bool]:
    """Return (hook sentence, used_fallback)."""
    fields = fields_for(lead, campaign)
    fallback = render(campaign.personalization.hook_fallback, fields).text
    if not has_personal_data(lead):
        return fallback, True
    prompt = HOOK_PROMPT.format(
        agent_name=campaign.agent_name,
        first_name=fields.get("first_name") or "there",
        title=fields.get("title") or "-",
        company=fields.get("company") or "-",
        headline=sanitize_user_text(fields.get("headline") or "-", 300),
        post_block=sanitize_user_text(_post_block(lead), 800),
        max_chars=HOOK_MAX_CHARS,
    )
    for attempt in range(2):
        try:
            text = _clean_llm_text(
                await llm.complete(prompt, temperature=0.7 if attempt == 0 else 0.4)
            )
        except Exception:
            break
        if not check_hook(text):
            return text, False
    return fallback, True


async def render_message(
    name: str, lead: LeadRecord, campaign: Campaign, llm: TextLLM | None
) -> Rendered:
    """Render campaign.messages[name] for a lead, filling {hook} if requested."""
    template = campaign.messages.get(name)
    if template is None:
        raise KeyError(f"campaign has no message template {name!r}")
    fields = fields_for(lead, campaign)
    hook_used = hook_fallback = False
    if HOOK_FIELD in template_fields(template):
        if campaign.personalization.hook == "none" or llm is None:
            fields[HOOK_FIELD] = ""
        else:
            hook, hook_fallback = await draft_hook(lead, campaign, llm)
            fields[HOOK_FIELD] = hook
            hook_used = not hook_fallback
    rendered = render(template, fields)
    rendered.hook_used = hook_used
    rendered.hook_fallback_used = hook_fallback
    limit = LENGTH_LIMITS.get(name)
    if limit and len(rendered.text) > limit:
        rendered.warnings.append(
            f"{name} renders to {len(rendered.text)} chars (guideline {limit})"
        )
    return rendered


async def draft_comment(
    post: PostRef, lead: LeadRecord, campaign: Campaign, llm: TextLLM, max_sentences: int = 3
) -> tuple[str | None, list[str]]:
    """Return (comment or None, problems of the last attempt)."""
    fields = fields_for(lead, campaign)
    title_line = ", ".join(p for p in (campaign.company_name,) if p) or "a fellow practitioner"
    prompt = COMMENT_PROMPT.format(
        agent_name=campaign.agent_name,
        title_line=title_line,
        first_name=fields.get("first_name") or "the author",
        headline=sanitize_user_text(fields.get("headline") or "-", 200),
        post_text=sanitize_user_text(post.text, 1500),
        max_sentences=max_sentences,
    )
    problems: list[str] = []
    for attempt in range(2):
        try:
            text = _clean_llm_text(
                await llm.complete(prompt, temperature=0.8 if attempt == 0 else 0.5)
            )
        except Exception as e:  # network / model error
            return None, [f"llm error: {e}"]
        problems = check_comment(text, max_sentences, campaign)
        if not problems:
            return text, []
    return None, problems


# ── campaign validation ───────────────────────────────────────────────────


def campaign_check(
    campaign: Campaign, sample_custom: set[str] | None = None
) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for the templates and personalization settings."""
    errors: list[str] = []
    warnings: list[str] = []
    allowed = allowed_fields(campaign, sample_custom)
    used_templates: set[str] = set()
    for step in campaign.steps:
        for key in Campaign.TEMPLATE_PARAM_KEYS:
            v = step.params.get(key)
            if v:
                used_templates.add(str(v))
    for name, template in campaign.messages.items():
        fields = template_fields(template)
        unknown = sorted(f for f in fields if f not in allowed)
        # {custom_x} can only come from a CSV column; without the CSV we cannot verify it,
        # so it is a warning here and an error at import time (which passes the columns).
        if sample_custom is None:
            csv_only = [f for f in unknown if f.startswith("custom_")]
            unknown = [f for f in unknown if not f.startswith("custom_")]
            if csv_only:
                cols = ", ".join(f[len("custom_") :] for f in csv_only)
                warnings.append(
                    f"messages.{name}: {csv_only} need CSV column(s) {cols}; "
                    "checked again at import"
                )
        if unknown:
            errors.append(f"messages.{name}: unknown placeholder(s) {unknown}")
        if (
            name in used_templates
            and name.startswith("m")
            and not (fields - {"agent_name", "company_name", "booking_link"})
        ):
            warnings.append(
                f"messages.{name}: nothing varies per person (no merge field or {{hook}}); "
                "LinkedIn filters identical copy"
            )
        if HOOK_FIELD in fields and campaign.personalization.hook == "none":
            warnings.append(
                f"messages.{name}: uses {{hook}} but personalization.hook is 'none'; "
                "the line will be dropped"
            )
        limit = LENGTH_LIMITS.get(name)
        if limit and len(template) > limit + 40:
            warnings.append(
                f"messages.{name}: template is {len(template)} chars, guideline {limit}"
            )
    missing = sorted(t for t in used_templates if t not in campaign.messages)
    if missing:
        errors.append(f"steps reference undefined templates {missing}")
    fb_unknown = sorted(
        f for f in template_fields(campaign.personalization.hook_fallback) if f not in allowed
    )
    if fb_unknown:
        errors.append(f"personalization.hook_fallback: unknown placeholder(s) {fb_unknown}")
    if not campaign.agent_name:
        errors.append("agent_name is required")
    return errors, warnings


def context_for_review(post: PostRef | None, lead: LeadRecord) -> dict[str, Any]:
    return {
        "lead": lead.display_name,
        "headline": lead.profile.get("headline", ""),
        "post_url": post.url if post else "",
        "post_age_days": post.posted_days_ago if post else None,
        "post_text": post.text if post else "",
    }
