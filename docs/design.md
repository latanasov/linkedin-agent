# LinkedIn Local Agent — Detailed Design (Shape B, v2)

Design document. This agent was extracted from a hosted SaaS pipeline (the "cloud worker"
referred to below); the comparisons explain why things are built the way they are.
and `LINKEDIN_CADENCE_RESEARCH.md` (what the outreach data says, the playbook we
implement). This doc answers **"what does the standalone local agent look like"**:
package layout, data model, interfaces, the sequence engine, flows, CLI, tests, and a
build order.

v2 changes from v1: the fixed visit → connect → DM flow is replaced by a **sequence
engine** that runs the research playbook (warm-up touches, blank-or-earned note,
recipient-local send windows, question-first follow-ups, reply detection, invite
withdrawal, account ramp and an acceptance-rate governor). Five new browser actions.
One extra week of build.

Status: **design, not implemented**.

---

## 0. One-paragraph summary

`linkedin-agent` is a pip-installable Python package at the repository root. You run
`linkedin-agent login` once: a real, headed Chrome opens, you sign in, and the profile
is kept under `~/.linkedin-agent/profiles/<account>/`. You `import` a CSV of leads into
a campaign whose YAML defines a sequence (default: the research playbook). `run` keeps
a single process alive that, every few minutes, materialises the sequence steps that
are due into tasks, executes them one at a time in the browser with human pacing, and
advances each lead's sequence on the result. Comments are written by the model from
the prospect's post; DMs are your own templates, rendered per lead with merge fields and
one optional model-filled sentence. Any reply from a prospect stops their sequence and
lands in `inbox`. Everything lives in one SQLite file; nothing leaves the machine except
the LLM calls.

---

## 1. What changes versus the cloud worker

| Concern | Cloud worker today | Local agent v2 |
|---|---|---|
| Auth | paste `li_at` + UA, encrypted in Redis, injected via `storage_state` | headed Chrome, persistent `user_data_dir`; cookie import kept as `login --cookie` fallback |
| Cookie renewal, tab cleanup | both silent no-ops on browser-use 0.11 (`get_storage_state` and `browser_context` do not exist) | Chrome persists cookies itself; tabs closed via `get_tabs()` + CDP |
| Outreach logic | hard-coded Day-0 visit+connect, scheduler DM after 2 days | **sequence engine** driven by `campaign.yaml`; default sequence = research playbook |
| Actions | visit, connect, message, inmail | + `follow`, `like_post`, `comment_post`, `check_connection`, `check_replies`, `withdraw_invite` |
| Send timing | whenever the task pops, server time | recipient-local windows, Tue–Thu preference for invites/messages, weekends off |
| Limits | daily counter per action, tier table | daily **and rolling 7-day** caps from an action log, account **ramp** over 4 weeks, **acceptance-rate governor** |
| Task queue, lead state, breaker | Redis + Postgres | one SQLite file |
| Message inputs | `Agent` columns | `campaign.yaml` |
| LLM | LangChain `ChatOpenAI` + browser-use `ChatOpenAI` | `browser_use.ChatOpenRouter` for the browser, a 20-line `httpx` client for text; no LangChain |
| Reply handling | none | `check_replies` stops the sequence; `inbox` lists leads waiting on you |
| Message text | worker builds InMail with LLM, scheduler DM is a hard-coded string | **user-written templates** with merge fields + one optional model-filled `{hook}` sentence; comments fully model-written |

Bugs fixed on the way (numbering from the plan doc §1.6): 1 (re-check sends a
connect), 2 (quota consumed on failure), 3 (`recent_posts` never scraped), 5 (step
numbering), 6 (failure counter in memory).

---

## 2. Package layout

```
linkedin-agent/
├── pyproject.toml
├── README.md
├── campaigns/
│   ├── default.yaml                 # the research playbook (§5)
│   ├── inmail.yaml                  # Sales Navigator variant
│   └── cold-minimal.yaml            # today's visit→connect→DM, for comparison runs
├── linkedin_agent/
│   ├── config.py                    # Settings, caps, ramp table
│   ├── models.py                    # Task, TaskResult, LeadRecord, Campaign, SequenceStep, Action
│   ├── ports.py                     # Protocols: TaskQueue, LeadStore, ActionLog, CircuitBreaker, TaskExecutor, ReviewQueue
│   ├── llm.py                       # make_browser_llm(), make_text_llm()
│   ├── core/
│   │   ├── browser_pool.py          # profile-dir aware
│   │   ├── prompts.py               # moved: LINKEDIN_URL_RE, sanitize_user_text, run_linkedin_agent(prompt, browser, llm)
│   │   ├── tasks/                   # one file per Action, each builds a prompt and returns a dict
│   │   │   ├── visit_profile.py     # + location, last 3 posts (url, date, text), activity recency, company page url
│   │   │   ├── follow.py            # NEW
│   │   │   ├── like_post.py         # NEW, takes post_url
│   │   │   ├── comment_post.py      # NEW, takes post_url + approved comment text
│   │   │   ├── check_connection.py  # NEW, read-only: Message / Pending / Connect
│   │   │   ├── send_connection.py   # moved; note may be empty
│   │   │   ├── withdraw_invite.py   # NEW
│   │   │   ├── check_replies.py     # NEW, read-only: open thread, has prospect replied after our last msg?
│   │   │   ├── send_message.py      # moved, step numbering fixed
│   │   │   └── send_inmail.py       # moved
│   │   ├── status_map.py            # map_result_to_status(action, result)
│   │   ├── errors.py                # classify_error(exc) -> ErrorKind
│   │   ├── messages.py              # draft_comment(), draft_message(step, lead, campaign, llm), template fallbacks
│   │   ├── limits.py                # effective_cap(action, account_age, governor_state), window checks
│   │   ├── timezone.py              # location string -> tz (small lookup table + LLM fallback), send-window math
│   │   ├── sequence.py              # THE ENGINE: advance(lead_seq, result) and due_steps(now)
│   │   └── runner.py                # process_task(...), run_loop(...)
│   ├── adapters/
│   │   ├── sqlite/
│   │   │   ├── schema.sql
│   │   │   ├── db.py
│   │   │   ├── tasks.py             # SqliteTaskQueue
│   │   │   ├── leads.py             # SqliteLeadStore (+ lead_sequences)
│   │   │   ├── action_log.py        # SqliteActionLog (daily + rolling-7d counts, acceptance rate)
│   │   │   ├── safety.py            # SqliteCircuitBreaker
│   │   │   └── review.py            # SqliteReviewQueue (drafted comments/messages awaiting approval)
│   │   ├── browser_use_executor.py  # TaskExecutor over browser-use
│   │   └── csv_import.py
│   ├── scheduler.py                 # tick(): materialise due steps into tasks, schedule withdrawals, run governor
│   └── cli.py                       # typer app
└── tests/                           # see §8
```

Dependencies: `browser-use==0.11.3` (pinned), `playwright`, `typer`, `pydantic`,
`pydantic-settings`, `aiosqlite`, `httpx`, `pyyaml`, `psutil`, `tzdata`. Dev: `pytest`,
`pytest-asyncio`, `freezegun`, `ruff`, `mypy`.

---

## 3. Data model

### 3.1 Enums and records (`models.py`)

```python
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


class LeadStage(StrEnum):  # replaces the 4-value linkedin_status
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


class PostRef(BaseModel):
    url: str
    posted_at: date | None
    text: str  # first 300 chars
    liked: bool = False
    commented: bool = False


class LeadRecord(BaseModel):
    id: str
    campaign: str
    linkedin_url: str
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    title: str | None = None
    email: str | None = None
    location: str | None = None
    timezone: str | None = None  # IANA, from location
    custom_fields: dict[str, Any] = {}
    profile: dict[str, Any] = {}  # headline, about, connection_degree, company_page_url
    posts: list[PostRef] = []
    stage: LeadStage = LeadStage.NEW
    invited_at: datetime | None = None
    connected_at: datetime | None = None
    last_touch_at: datetime | None = None
    touches_48h: int = 0
    last_message_at: datetime | None = None
    replied_at: datetime | None = None


class SequenceStep(BaseModel):
    id: str  # "warm.like", "post.m1"
    action: Action
    after: str = "0d"  # delay from previous step ("2d", "6h") — minimum, jitter added
    branch: Literal["any", "posts", "quiet"] = "any"
    window: Literal["engage", "send", "any"] = "any"
    params: dict[str, Any] = {}  # e.g. note: "earned" | "blank", max_chars, requires_review
    on_result: dict[str, str] = {}  # result status -> next step id or "end:<stage>"


class Campaign(BaseModel):
    name: str
    agent_name: str
    company_name: str = ""
    value_proposition: str
    booking_link: str = ""
    mode: Literal["standard", "inmail"] = "standard"
    default_timezone: str = "Europe/Sofia"
    quiet_threshold_days: int = 30  # no post in N days -> "quiet" branch
    withdraw_after_days: int = 21
    review_comments: bool = False  # comments are model-written; opt in to approve each
    messages: dict[str, str]  # user-written templates: connection_note, m1..m3, inmail_* (§5.4)
    personalization: Personalization = Personalization()  # hook: none | one_sentence, hook_fallback
    steps: list[SequenceStep]


class Task(BaseModel):  # same shape as the Redis payload today, plus step_id
    id: str
    lead_id: str | None
    step_id: str | None
    action: Action
    profile_url: str
    account: str
    params: dict[str, Any] = {}
    status: Literal["queued", "running", "done", "failed", "skipped", "awaiting_review"] = "queued"
    attempts: int = 0
    not_before: datetime | None = None
    not_after: datetime | None = None


class TaskResult(BaseModel):
    status: str
    error: str | None = None
    data: dict[str, Any] = {}
    error_kind: ErrorKind | None = None
```

### 3.2 SQLite schema

```sql
CREATE TABLE leads (
  id TEXT PRIMARY KEY, campaign TEXT NOT NULL, linkedin_url TEXT NOT NULL UNIQUE,
  first_name TEXT, last_name TEXT, company TEXT, title TEXT, email TEXT,
  location TEXT, timezone TEXT,
  custom_fields TEXT NOT NULL DEFAULT '{}', profile TEXT NOT NULL DEFAULT '{}', posts TEXT NOT NULL DEFAULT '[]',
  stage TEXT NOT NULL DEFAULT 'new',
  invited_at TEXT, connected_at TEXT, last_touch_at TEXT, last_message_at TEXT, replied_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE lead_sequences (                      -- one row per lead: where it is in the campaign sequence
  lead_id TEXT PRIMARY KEY REFERENCES leads(id),
  campaign TEXT NOT NULL, step_id TEXT,            -- current step; NULL when finished
  branch TEXT,                                     -- posts | quiet, decided after visit
  next_due_at TEXT,                                -- when the current step may be materialised
  history TEXT NOT NULL DEFAULT '[]'               -- JSON [{step_id, task_id, result, at}]
);

CREATE TABLE tasks (
  id TEXT PRIMARY KEY, lead_id TEXT REFERENCES leads(id), step_id TEXT,
  action TEXT NOT NULL, profile_url TEXT NOT NULL, account TEXT NOT NULL,
  params TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
  not_before TEXT, not_after TEXT, result TEXT,
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE INDEX tasks_pick ON tasks(status, not_before, created_at);

CREATE TABLE action_log (                          -- every executed action; source for all limits and metrics
  id INTEGER PRIMARY KEY, account TEXT NOT NULL, action TEXT NOT NULL,
  lead_id TEXT, at TEXT NOT NULL, ok INTEGER NOT NULL, result_status TEXT
);
CREATE INDEX action_log_window ON action_log(account, action, at);

CREATE TABLE review_queue (                        -- LLM drafts waiting for a human
  task_id TEXT PRIMARY KEY REFERENCES tasks(id),
  kind TEXT NOT NULL,                              -- comment | message | note
  context TEXT NOT NULL,                           -- JSON: post text, lead summary
  draft TEXT NOT NULL, approved_text TEXT, decided_at TEXT
);

CREATE TABLE accounts (
  name TEXT PRIMARY KEY, first_action_at TEXT, logged_in_at TEXT, user_agent TEXT,
  tripped_until TEXT, trip_reason TEXT, consecutive_failures INTEGER NOT NULL DEFAULT 0,
  session_expired_at TEXT, governor_state TEXT NOT NULL DEFAULT 'normal'   -- normal | halved | paused
);

CREATE TABLE schema_version (version INTEGER NOT NULL);
```

`daily_counts` from v1 is gone: `action_log` gives daily counts, rolling 7-day counts,
per-prospect 48-hour touch counts and the acceptance rate from one table.

---

## 4. Interfaces (`ports.py`)

```python
class TaskQueue(Protocol):
    async def enqueue(self, task: Task) -> None: ...
    async def claim_next(self, account: str, now: datetime) -> Task | None: ...   # queued, not_before<=now<not_after, oldest first
    async def finish(self, task_id: str, result: TaskResult, status: str) -> None: ...
    async def expire_overdue(self, now: datetime) -> int: ...                     # not_after passed -> skipped (window missed)
    async def requeue_stale_running(self, older_than: timedelta) -> int: ...

class LeadStore(Protocol):
    async def get(self, lead_id: str) -> LeadRecord | None: ...
    async def upsert_many(self, leads: list[LeadRecord]) -> int: ...
    async def update(self, lead: LeadRecord) -> None: ...
    async def get_sequence(self, lead_id: str) -> LeadSequence | None: ...
    async def save_sequence(self, seq: LeadSequence) -> None: ...
    async def due_sequences(self, now: datetime, campaign: str | None) -> list[tuple[LeadRecord, LeadSequence]]: ...
    async def by_stage(self, stage: LeadStage, campaign: str | None) -> list[LeadRecord]: ...

class ActionLog(Protocol):
    async def record(self, account: str, action: Action, lead_id: str | None, ok: bool, result_status: str | None) -> None: ...
    async def count(self, account: str, action: Action, since: datetime) -> int: ...
    async def touches(self, lead_id: str, since: datetime) -> int: ...
    async def acceptance_rate(self, account: str, since: datetime) -> float | None: ...  # connected / invited in window

class CircuitBreaker(Protocol):        # unchanged from v1, backed by the accounts table
class ReviewQueue(Protocol):
    async def submit(self, task_id: str, kind: str, context: dict, draft: str) -> None: ...
    async def pending(self) -> list[ReviewItem]: ...
    async def decide(self, task_id: str, approved_text: str | None) -> None: ...      # None = reject -> task skipped

class TaskExecutor(Protocol):
    async def execute(self, task: Task, browser: Any) -> TaskResult: ...
```

---

## 5. The sequence engine

### 5.1 Default campaign (`campaigns/default.yaml`)

This is §2 of the research doc expressed as data. Delays are minimums; the engine adds
0–40% jitter and then snaps forward to the next allowed window.

```yaml
name: default
mode: standard
quiet_threshold_days: 30
withdraw_after_days: 21
review_comments: false          # comments are model-written; set true to approve each one
messages: {...}                 # user-written templates, see §5.4
steps:
  # ── warm-up ────────────────────────────────────────────────
  - {id: warm.visit,   action: visit,          after: 0d}
  - {id: warm.follow,  action: follow,         after: 1d, window: engage}
  - {id: warm.like,    action: like_post,      after: 1d, window: engage, branch: posts,
     params: {pick: newest, max_age_days: 30}}
  - {id: warm.comment, action: comment_post,   after: 2d, window: engage, branch: posts,
     params: {pick: different_from_liked, max_sentences: 3}}
  # ── invite ─────────────────────────────────────────────────
  - {id: invite.posts, action: connect,        after: 2d, window: send, branch: posts,
     params: {note: connection_note},
     on_result: {sent: wait.accept, already_connected: post.m1, already_pending: wait.accept}}
  - {id: invite.quiet, action: connect,        after: 2d, window: send, branch: quiet,
     params: {note: connection_note_quiet},
     on_result: {sent: wait.accept, already_connected: post.m1, already_pending: wait.accept}}
  # ── wait for acceptance ────────────────────────────────────
  - {id: wait.accept,  action: check_connection, after: 1d, window: any,
     params: {repeat_every: 1d, until_days: 21},
     on_result: {connected: post.m1, pending: wait.accept, not_connected: wait.accept, timeout: withdraw}}
  - {id: withdraw,     action: withdraw_invite, after: 0d, on_result: {withdrawn: "end:not_accepted"}}
  # ── post-acceptance ────────────────────────────────────────
  - {id: post.m1, action: message, after: 0d, window: send,
     params: {template: m1}}
  - {id: post.r1, action: check_replies, after: 3d, params: {repeat_every: 1d, until_step: post.m2},
     on_result: {replied: "end:replied", none: post.m2}}
  - {id: post.m2, action: message, after: 0d, window: send,
     params: {template: m2}}
  - {id: post.r2, action: check_replies, after: 5d, on_result: {replied: "end:replied", none: post.m3}}
  - {id: post.m3, action: message, after: 0d, window: send,
     params: {template: m3}}
  - {id: post.r3, action: check_replies, after: 7d, on_result: {replied: "end:replied", none: "end:nurture"}}
```

`inmail.yaml` swaps `invite.*` + `wait.accept` for a single `inmail` step on day 5 with
`params: {template: inmail_body, subject: inmail_subject}` and then reuses `post.r1` onward. `cold-minimal.yaml` is
today's cloud flow, kept so the two can be run side by side and measured.

### 5.2 Engine rules (`core/sequence.py`)

- **Branch decision** happens once, after `warm.visit`: newest post within
  `quiet_threshold_days` → `posts`, else `quiet`. Steps whose `branch` does not match
  are skipped without delay.
- **Windows** (`core/timezone.py`), all in the lead's timezone, falling back to the
  campaign default:
  - `send`: Tue–Thu, 08:30–11:00 and 14:00–16:00
  - `engage`: Mon–Fri, 09:00–18:00
  - `any`: Mon–Sat, 08:00–20:00
  A step due outside its window gets `not_before` = next window open and `not_after` =
  that window's close plus the following window; a task that misses both is expired
  and re-materialised for the next window rather than executed late at night.
- **Per-prospect spacing**: a step is not materialised if the lead already had a touch
  today, or two touches in the last 48h. Likes and comments never target the same post.
- **Review gate** (optional): with `review_comments: true`, comment steps are created
  as `awaiting_review`, the draft goes into `review_queue`, and the task becomes
  `queued` only after `linkedin-agent review` approves it. Rejected → step skipped,
  sequence continues. Messages never go through review: they are the user's own
  templates, checked with `preview` before the campaign starts.
- **Reply detection**: `check_replies` runs before every `message` step regardless of
  the YAML (cheap, read-only) and daily for leads in `messaging`. A reply ends the
  sequence with stage `replied`; `inbox` shows them.
- **Terminal `end:<stage>`** sets the lead stage and clears `step_id`. `nurture` leads
  get a `like_post` every 30 days for 90 days from a tiny built-in sequence, then
  `done`.
- **Result mapping** stays a pure function; `on_result` in the YAML only chooses the
  next step, `status_map.py` decides the stage side effects (e.g. `connected` sets
  `connected_at`).

### 5.3 Limits, ramp and governor (`core/limits.py`)

```python
BASE_CAPS = {  # per account per day, and rolling 7d where it matters
    Action.CONNECT: (20, 90),
    Action.MESSAGE: (40, None),
    Action.INMAIL: (20, None),
    Action.VISIT: (60, None),
    Action.LIKE_POST: (30, None),
    Action.COMMENT_POST: (8, None),
    Action.FOLLOW: (15, None),
    Action.WITHDRAW_INVITE: (15, None),
    Action.CHECK_CONNECTION: (60, None),
    Action.CHECK_REPLIES: (60, None),
}
RAMP = [
    (7, 0.25),
    (14, 0.40),
    (21, 0.60),
    (28, 0.80),
]  # (account_age_days_lt, multiplier), then 1.0


def effective_cap(action, account_age_days, governor_state, user_cap) -> tuple[int, int | None]:
    day, week = BASE_CAPS[action]
    m = next((mult for lt, mult in RAMP if account_age_days < lt), 1.0)
    if action in (CONNECT, INMAIL) and governor_state == "halved":
        m *= 0.5
    if action in (CONNECT, INMAIL) and governor_state == "paused":
        return (0, 0)
    day = min(max(1, round(day * m)), user_cap or day)
    return day, (None if week is None else round(week * m))
```

`scheduler.tick()` recomputes the governor once a day from `action_log`: 7-day
acceptance rate < 30% → `halved`, < 20% → `paused` (and prints a warning), ≥ 35% for
7 days → back to `normal`. Tier ceilings from the cloud `rate_limiter.py` are kept as
an optional upper bound (`tier:` in settings) so the local caps can never exceed what
the product allows.

### 5.4 Content: user-written messages, model-written comments (`core/messages.py`)

Two different rules for two different kinds of text.

**Messages, notes and InMails are written by the user, once, in the campaign file.**
The agent never invents a DM. It renders the user's template for each lead with merge
fields and, if the template asks for it, one model-filled personalisation sentence.

```yaml
messages:
  connection_note: "Enjoyed your post on {post_topic} — glad to stay in touch."
  connection_note_quiet: ""                      # blank for the quiet branch
  m1: |
    Hi {first_name}, thanks for connecting.
    {hook}
    Quick question — how does your team keep an eye on cloud spend today?
  m2: |
    One thing that helped teams like {company}: seeing which service drives the bill
    before the invoice arrives. Happy to share how if useful.
  m3: |
    If it's worth a 15-minute look, grab a slot here:
    {booking_link}
  inmail_subject: "Quick question, {first_name}"
  inmail_body: |
    Hi {first_name},
    {hook}
    I'm {agent_name} at {company_name} — we help engineering teams cut their cloud bill.
    Worth a quick chat?
personalization:
  hook: one_sentence        # none | one_sentence
  hook_fallback: "Noticed your work at {company}."
```

- **Merge fields** come from the lead row and the visit scrape: `{first_name}`,
  `{last_name}`, `{company}`, `{title}`, `{headline}`, `{post_topic}`, `{agent_name}`,
  `{company_name}`, `{booking_link}`, and any `custom_*` CSV column as `{custom_x}`.
  An unknown field fails `campaign check`; a field empty for a given lead falls back to
  a per-field default (`{company}` → "your company", `{first_name}` → "there") and the
  render is flagged in `preview`.
- **`{hook}`** is the only model-written part of a message: one sentence, ≤120 chars,
  drawn from the scraped post/headline/about, no links, no product names, no question.
  If the lead has no usable data or the draft fails the checks, `hook_fallback` is
  rendered instead. `personalization: none` removes the slot entirely and the message
  is pure template.
- **Identical-copy guard.** LinkedIn's spam filter targets identical message bodies.
  `campaign check` warns if a template contains no merge field and no `{hook}`; the
  runner refuses to send a body identical to one sent in the last 7 days unless the
  template is explicitly marked `allow_identical: true`.
- **Length checks** still apply (m1 ≤300 chars after rendering, m2 ≤250, m3 ≤220,
  note ≤150) and are warnings, not errors, since the text is the user's.
- **`linkedin-agent preview <lead>`** renders every message for one lead exactly as it
  will be sent, so the user can see the templates and the hook together before the
  first send.

**Comments are fully model-written.** The user has no template for them; the model
reads the post and writes the comment from the campaign context.

| Inputs | Rules enforced in the prompt and checked in code |
|---|---|
| post text, lead headline, `value_proposition` (for context only) | 1–3 sentences; must reference a concrete thing from the post; no links, no product or company names, no "great post"/"so true"/"thanks for sharing"; no question that is really a pitch. Drafts failing the checks are re-drafted once, then the step is skipped |

Review is optional (`review_comments: false` by default, switch on to approve each
one in `linkedin-agent review`). The banned-phrase and length checks are code, not
prompt instructions, and are unit-tested.

---

## 6. Flows

### 6.1 `login` — unchanged from v1

Headed Chrome on the profile dir, you sign in, the agent confirms `/feed/` loads and
records `user_agent` and `logged_in_at`. `--cookie` fallback sets `li_at` via CDP once
and forgets the value.

### 6.2 `import`

```
linkedin-agent import leads.csv --campaign campaigns/default.yaml
```

Validates URLs, upserts leads, infers `timezone` from a `location`/`country` column if
present (else the campaign default; the visit step refines it from the profile), and
creates a `lead_sequences` row at `warm.visit` with `next_due_at = now`. No tasks are
created yet; the scheduler materialises them.

### 6.3 `run` — one process, two loops

```
linkedin-agent run [--account default] [--headless] [--once]
```

- **Scheduler loop** (every 5 min): `expire_overdue()`, `requeue_stale_running()`,
  materialise due steps into tasks (respecting spacing, caps and windows so the queue
  never holds more than the day can execute), schedule withdrawals for invites older
  than `withdraw_after_days`, run the governor once a day, emit drafts to the review
  queue.
- **Executor loop**: `claim_next()`, `process_task()` (the nine steps from v1 §5.3 with
  `action_log.record()` added and `check_*` tasks exempt from the human-pacing delay),
  sleep 45–180s between browser actions, close the browser after 30 min idle.

`--once` runs one scheduler tick and drains what is claimable now, then exits — the
mode for a cron/launchd job if you would rather not keep a terminal open.

### 6.4 `review`

```
linkedin-agent review            # interactive: shows post excerpt + draft, [a]pprove / [e]dit / [r]eject / [s]kip
linkedin-agent review --list
```

Approving moves the task to `queued` with the final text in `params`. This is the
human-in-the-loop that keeps comments from reading as bot output; the research is
unambiguous that a bad comment is worse than none.

### 6.5 `inbox`, `status`, `report`

- `inbox`: leads in `replied`, newest first, with the last message we sent and a link to
  the thread. Marking one `--handled` moves it to `done`.
- `status`: login/breaker/governor state, today's and this week's usage against caps,
  review queue size, queue depth, last 10 results.
- `report [--campaign X] [--since 7d]`: warm-up completion, invites sent, acceptance
  rate (7d and cumulative), median time to accept, M1 reply rate, overall reply rate,
  withdrawals, leads per stage. Benchmarks to beat printed alongside (28.5% / 10.4%).

### 6.6 `preview` and `campaign check`

`campaign check <file>` validates step ids, merge fields and template lengths and
warns about templates with no varying content. `preview <lead>` renders the
connection note and every message for one lead, with the `{hook}` the model would
insert, so the user sees exactly what will be sent before `run`.

### 6.7 One-off commands

`visit`, `follow`, `like`, `comment`, `connect`, `message`, `inmail`, `withdraw`,
`check` each take a URL and run immediately (still logged and still capped).

---

## 7. browser-use vs Chrome DevTools MCP

The question came up mid-design and it is worth settling explicitly.

**What each is.** browser-use is a Python library: an LLM agent loop (observe DOM →
choose action → act) with its own Playwright/CDP session, running inside our process.
Chrome DevTools MCP is Google's MCP server exposing a Chrome instance (navigate, click,
fill, accessibility snapshot, screenshot, evaluate, network) to an MCP client. The
"agent" in that model is whichever LLM host holds the MCP session, usually Claude Code
or a similar CLI, and it reasons per step over the snapshot. The repo owner already
runs outreach this way through the `run-outreach` skill with Claude in Chrome.

**Where DevTools MCP wins.** Page understanding per step is excellent because a frontier
model reads a clean accessibility tree, the tool surface is stable and Google-maintained
(unlike browser-use's churn, see plan doc §1.6.7), it attaches to your real Chrome
with your real profile and extensions, and it is the best environment for
*developing* the flows: you can watch Claude work a modal, then codify what it did.

**Where it loses for this agent.** The requirement is an unattended process that runs a
multi-week cadence for hundreds of leads, wakes every few minutes, respects windows and
caps, and keeps state. That is a scheduler with a browser attached, and an interactive
LLM session is the wrong host for it: each action would be a full Claude turn with a
large snapshot (slow and expensive at hundreds of actions a week), the session is not a
daemon, and the Python agent would have to either embed an MCP client plus its own LLM
loop (re-implementing browser-use) or shell out to Claude Code per task. Neither is
simpler than what browser-use already provides in-process.

**Decision.** browser-use in the Python process for unattended execution, behind the
`TaskExecutor` seam. Use Chrome DevTools MCP / Claude in Chrome for two things: as the
development and debugging bench when a task's prompt stops working on a new LinkedIn
layout, and for ad-hoc, human-supervised outreach where you want to watch each step.
Two follow-ups reduce LLM cost further without changing the architecture: a
deterministic CDP executor for the read-only and single-click actions
(`check_connection`, `check_replies`, `like_post`, `follow`, `withdraw_invite`) which
have stable `aria-label`s, keeping the LLM for `visit`, `connect`, `comment_post` and
`message` where modals vary; and, if browser-use's churn becomes a burden, the same
seam accepts a plain Playwright executor.

---

## 8. Testing strategy

Pure modules (`sequence.py`, `limits.py`, `timezone.py`, `messages.py` checks,
`status_map.py`, `errors.py`, `scheduler.py` selection logic) get direct unit tests
with `freezegun`. Browser code is tested through `FakeExecutor`.

- `test_sequence.py`: branch decision, step skipping by branch, jitter bounds, window
  snapping across days and DST, `on_result` routing including `already_connected`
  shortcut, terminal stages, nurture schedule, review gate state transitions.
- `test_limits.py`: ramp table by account age, governor states, user cap never raised,
  rolling-7d invite cap, spacing rules (one touch/day, two per 48h, never like and
  comment the same post).
- `test_timezone.py`: location → tz lookup and fallback, send-window math for each
  window type, weekend skip, `not_after` expiry.
- `test_messages.py`: template rendering with every merge field, unknown-field error,
  empty-field fallbacks, `{hook}` slot filled / fallback / removed under
  `personalization: none`, identical-copy guard, length warnings; comment banned
  phrases, length caps, re-draft once then skip.
- `test_scheduler.py`: materialises only what today's caps allow, withdrawal at 21
  days, governor recomputation from a seeded `action_log`, review-queue emission.
- `test_runner.py`: everything from v1 plus `action_log.record` on every outcome and
  the `check_*` exemption from pacing.
- `test_action_log.py`, `test_task_queue.py`, `test_lead_store.py`, `test_review.py`:
  SQLite adapters on a temp file.
- `test_cli.py`: `import`, `review --list`, `status`, `report`, `run --once` with fakes.
- `test_browser_use_surface.py`: pin guard (v1 §4.1).
- `campaigns/*.yaml` are loaded and validated in a test so a typo in a step id fails CI.

---

## 9. A day with the agent

```
$ linkedin-agent import leads.csv --campaign campaigns/default.yaml
  Imported 60 leads (60 new). Sequences created at warm.visit.

$ linkedin-agent run
  09:00 tick   materialised 12 tasks (visit ×12)   caps today: visits 15/60 (ramp wk1 ×0.25) …
  09:03 visit    Jane Doe      ok  posts=3 (newest 2d)  tz=America/New_York  → branch posts
  09:05 visit    Bob Smith     ok  posts=0               tz=Europe/Berlin     → branch quiet
  …
  (next day)
  09:00 tick   follow ×12 due (engage window); 4 comments drafted (review_comments: true in this run)
$ linkedin-agent review
  [1/4] Jane Doe — post (2d ago): "We cut onboarding time in half by…"
        draft: "Halving onboarding by removing the approval step is the part most teams skip — did the
                support load move somewhere else, or did it actually disappear?"
        [a]pprove [e]dit [r]eject [s]kip > a
  …
  (day 6, Tuesday 09:40 NY time)
  connect  Jane Doe   sent   note: "Enjoyed the onboarding thread — glad to stay in touch."
  connect  Bob Smith  sent   note: (blank)
  (day 9)
  check    Jane Doe   connected → post.m1 due in send window
  message  Jane Doe   sent   "Hi Jane, thanks for connecting. Curious — after you cut the approval step, what
                              did the team do with the time it freed up?"
  (day 12)
  check_replies Jane Doe   replied → stage replied
$ linkedin-agent inbox
  Jane Doe   replied 2h ago   thread: https://www.linkedin.com/messaging/thread/…
$ linkedin-agent report --since 14d
  invites 38 · accepted 17 (44.7%, benchmark 28.5%) · median accept 2.1d · M1 replies 6/17 (35%, benchmark 10.4%)
  withdrawn 0 · governor normal · caps: invites 12/20 today, 38/90 this week
```

---

## 10. Build order (four to five weeks)

**Week 1 — core, no browser.** Scaffold, settings, models, ports, SQLite schema and
adapters, move `prompts.py`/`tasks/`/`browser_pool.py`, extract `status_map`, `errors`,
`messages`, `limits`, `timezone`. `runner.py` with `FakeExecutor`. Tests green.

**Week 2 — sequence engine.** `sequence.py`, `scheduler.py`, campaign YAML loading and
validation, review queue, `import`, `run --once` end to end with fakes. `test_sequence`
and `test_scheduler` are the bulk of this week.

**Week 3 — real browser, existing actions.** `BrowserUseExecutor`, `login`, tab cleanup,
`visit` with posts/location, `connect` with blank note, `check_connection`, `message`,
`inmail`. Manual smoke on a real account at ramp week-1 caps.

**Week 4 — new actions and the human loop.** `follow`, `like_post`, `comment_post`,
`withdraw_invite`, `check_replies`; `review`, `inbox`, `status`, `report`. Second smoke
run through a full warm-up on ~10 leads.

**Week 5 — hardening and buffer.** Retry policy, `requeue_stale_running`, governor
in production, ruff/mypy strict, CI, README. Buffer for LinkedIn layout surprises. If
time remains: the deterministic CDP executor for the read-only/single-click actions.

Definition of done: a fresh machine goes from `pip install` to a completed
warm-up → invite → acceptance → M1 on real leads using only the README; the test suite
is green; `report` shows the numbers needed to compare
`default.yaml` against `cold-minimal.yaml`.

---

## 11. Decisions taken (say so if you disagree)

- **Sequence engine over hard-coded flow.** The research changes the flow in six
  places; encoding it as data means the next change is a YAML edit with a test.
- **DMs are the user's words; comments are the model's.** Messages are templates with
  merge fields and at most one model-filled sentence, so the user controls what is
  sent in their name. Comments are model-written because they must respond to a post
  the user has not read; code-level checks and an optional review gate contain the
  risk.
- **Blank note on the quiet branch, earned note on the posts branch.** The one
  reading of the conflicting data that all sources agree with.
- **No skill endorsements.** No evidence they help; several sources call them spammy.
- **Ramp and governor are on by default and only adjustable downward** except by
  editing settings explicitly. Restriction recovery takes weeks; a slow first month
  is cheaper.
- **browser-use stays, DevTools MCP is the dev bench** (§7).
- **Monorepo, pinned 0.11.3, no LangChain, SQLite** — unchanged from v1.
