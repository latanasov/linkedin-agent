"""Domain models shared by every layer. No I/O here."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Action(StrEnum):
    VISIT = "visit"
    FOLLOW = "follow"
    LIKE_POST = "like_post"
    COMMENT_POST = "comment_post"
    CONNECT = "connect"
    CHECK_CONNECTION = "check_connection"
    WITHDRAW_INVITE = "withdraw_invite"
    MESSAGE = "message"
    INMAIL = "inmail"
    CHECK_REPLIES = "check_replies"


# Actions the prospect can notice. Used for per-prospect spacing rules.
TOUCH_ACTIONS: frozenset[Action] = frozenset(
    {
        Action.VISIT,
        Action.FOLLOW,
        Action.LIKE_POST,
        Action.COMMENT_POST,
        Action.CONNECT,
        Action.MESSAGE,
        Action.INMAIL,
    }
)

# Read-only actions: no pacing delay needed, never count as a touch.
READ_ONLY_ACTIONS: frozenset[Action] = frozenset({Action.CHECK_CONNECTION, Action.CHECK_REPLIES})


class LeadStage(StrEnum):
    NEW = "new"
    WARMING = "warming"
    INVITED = "invited"
    CONNECTED = "connected"
    MESSAGING = "messaging"
    REPLIED = "replied"
    NURTURE = "nurture"
    NOT_ACCEPTED = "not_accepted"
    CANNOT_CONTACT = "cannot_contact"
    DONE = "done"
    PAUSED = "paused"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    AWAITING_REVIEW = "awaiting_review"


class ErrorKind(StrEnum):
    CRASH = "crash"
    SESSION_EXPIRED = "session_expired"
    RESTRICTED = "restricted"
    OTHER = "other"


class GovernorState(StrEnum):
    NORMAL = "normal"
    HALVED = "halved"
    PAUSED = "paused"


Branch = Literal["any", "posts", "quiet"]
# Not a Literal: "send", "engage" and "any" are built in, and a campaign may define its
# own under `windows:`. The Campaign validator is what rejects an unknown name.
Window = str


class WindowDef(BaseModel):
    """A campaign's own send window: which weekdays, and which hours on them."""

    days: list[str | int]
    hours: list[str]

    @model_validator(mode="after")
    def _validate(self) -> WindowDef:
        from .core.timezone import parse_days, parse_slots

        parse_days(self.days)
        parse_slots(self.hours)
        return self

    def to_spec(self) -> Any:
        from .core.timezone import WindowSpec, parse_days, parse_slots

        return WindowSpec(parse_days(self.days), parse_slots(self.hours))


def new_id() -> str:
    return str(uuid.uuid4())


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$")


def parse_duration(value: str | int | float) -> timedelta:
    """Parse '2d', '6h', '30m', '45s', '1w' (or a number of seconds) into a timedelta."""
    if isinstance(value, (int, float)):
        return timedelta(seconds=float(value))
    m = _DURATION_RE.match(value)
    if not m:
        raise ValueError(f"Invalid duration {value!r}; use e.g. '2d', '6h', '30m'")
    n, unit = int(m.group(1)), m.group(2)
    return {
        "s": timedelta(seconds=n),
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
        "w": timedelta(weeks=n),
    }[unit]


class PostRef(BaseModel):
    url: str = ""
    posted_days_ago: int | None = None
    text: str = ""
    liked: bool = False
    commented: bool = False

    def posted_on(self, seen_at: datetime) -> date | None:
        if self.posted_days_ago is None:
            return None
        return (seen_at - timedelta(days=self.posted_days_ago)).date()


class LeadRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    campaign: str
    linkedin_url: str
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    title: str | None = None
    email: str | None = None
    location: str | None = None
    timezone: str | None = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    profile: dict[str, Any] = Field(default_factory=dict)
    posts: list[PostRef] = Field(default_factory=list)
    stage: LeadStage = LeadStage.NEW
    invited_at: datetime | None = None
    connected_at: datetime | None = None
    last_touch_at: datetime | None = None
    last_message_at: datetime | None = None
    last_message_text: str | None = None
    # Their most recent message that already existed when we last sent one. A later
    # 'replied' verdict quoting this same text is stale history, not a new reply.
    prior_reply_text: str | None = None
    replied_at: datetime | None = None
    created_at: datetime | None = None

    @property
    def slug(self) -> str:
        """The /in/<slug> part of the URL, used to address a lead on the CLI."""
        m = re.search(r"/in/([^/?#]+)|/sales/(?:lead|people)/([^/?#,]+)", self.linkedin_url)
        if not m:
            return self.id
        return (m.group(1) or m.group(2)).rstrip("/")

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) if parts else self.slug


class SequenceStep(BaseModel):
    id: str
    action: Action
    after: str = "0d"
    branch: Branch = "any"
    window: Window = "any"
    params: dict[str, Any] = Field(default_factory=dict)
    on_result: dict[str, str] = Field(default_factory=dict)

    @field_validator("after")
    @classmethod
    def _validate_after(cls, v: str) -> str:
        parse_duration(v)
        return v

    @property
    def delay(self) -> timedelta:
        return parse_duration(self.after)

    @property
    def repeat_every(self) -> timedelta | None:
        v = self.params.get("repeat_every")
        return parse_duration(v) if v else None

    @property
    def until_days(self) -> int | None:
        v = self.params.get("until_days")
        return int(v) if v is not None else None


class Personalization(BaseModel):
    hook: Literal["none", "one_sentence"] = "one_sentence"
    hook_fallback: str = "Noticed your work at {company}."


class Campaign(BaseModel):
    name: str
    agent_name: str
    company_name: str = ""
    value_proposition: str = ""
    booking_link: str = ""
    mode: Literal["standard", "inmail"] = "standard"
    default_timezone: str = "UTC"
    quiet_threshold_days: int = 30
    withdraw_after_days: int = 21
    review_comments: bool = False
    windows: dict[str, WindowDef] = Field(default_factory=dict)
    messages: dict[str, str] = Field(default_factory=dict)
    personalization: Personalization = Field(default_factory=Personalization)
    steps: list[SequenceStep]

    @property
    def window_specs(self) -> dict[str, Any]:
        """This campaign's windows, by name. Empty when it uses only the built-in three."""
        return {name: w.to_spec() for name, w in self.windows.items()}

    @model_validator(mode="after")
    def _validate_steps(self) -> Campaign:
        from .core.timezone import WINDOWS

        known_windows = {*WINDOWS, *self.windows}
        for s in self.steps:
            if s.window not in known_windows:
                raise ValueError(
                    f"Step {s.id}: unknown window {s.window!r}; "
                    f"define it under `windows:` or use one of {', '.join(sorted(known_windows))}"
                )
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"Duplicate step ids: {dupes}")
        known = set(ids)
        for s in self.steps:
            for target in s.on_result.values():
                if target.startswith("end:"):
                    stage = target[4:]
                    if stage not in LeadStage.__members__.values():
                        raise ValueError(f"Step {s.id}: unknown end stage {stage!r}")
                elif target not in known:
                    raise ValueError(f"Step {s.id}: on_result points to unknown step {target!r}")
            for key in ("template", "note_template", "subject_template"):
                name = s.params.get(key)
                if name and name not in self.messages:
                    raise ValueError(f"Step {s.id}: {key} {name!r} not defined in messages")
        return self

    TEMPLATE_PARAM_KEYS: ClassVar[tuple[str, ...]] = (
        "template",
        "note_template",
        "subject_template",
    )

    def step(self, step_id: str) -> SequenceStep:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)

    def step_index(self, step_id: str) -> int:
        for i, s in enumerate(self.steps):
            if s.id == step_id:
                return i
        raise KeyError(step_id)


class LeadSequence(BaseModel):
    lead_id: str
    campaign: str
    step_id: str | None
    branch: Literal["posts", "quiet"] | None = None
    next_due_at: datetime | None = None
    step_entered_at: datetime | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


class Task(BaseModel):
    id: str = Field(default_factory=new_id)
    lead_id: str | None = None
    step_id: str | None = None
    action: Action
    profile_url: str
    account: str
    params: dict[str, Any] = Field(default_factory=dict)
    status: TaskStatus = TaskStatus.QUEUED
    attempts: int = 0
    not_before: datetime | None = None
    not_after: datetime | None = None
    body_hash: str | None = None
    result: dict[str, Any] | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskResult(BaseModel):
    status: str
    error: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    error_kind: ErrorKind | None = None

    @classmethod
    def from_raw(cls, raw: Any) -> TaskResult:
        """Normalise whatever the browser task returned into a TaskResult."""
        if isinstance(raw, TaskResult):
            return raw
        if isinstance(raw, dict):
            status = str(raw.get("status") or ("failed" if raw.get("error") else "ok"))
            error = raw.get("error")
            data = {k: v for k, v in raw.items() if k not in ("status", "error")}
            return cls(status=status, error=str(error) if error else None, data=data)
        if raw is None:
            return cls(status="failed", error="no_result")
        return cls(status="failed", error=f"unparseable_result: {str(raw)[:200]}")


class ReviewItem(BaseModel):
    task_id: str
    kind: str
    context: dict[str, Any]
    draft: str
    approved_text: str | None = None
    decided_at: datetime | None = None


class AccountState(BaseModel):
    name: str
    first_action_at: datetime | None = None
    logged_in_at: datetime | None = None
    user_agent: str | None = None
    tripped_until: datetime | None = None
    trip_reason: str | None = None
    consecutive_failures: int = 0
    session_expired_at: datetime | None = None
    governor_state: GovernorState = GovernorState.NORMAL
    governor_checked_at: datetime | None = None
