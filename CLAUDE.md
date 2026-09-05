# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A single Python package, `linkedin_agent/`, that runs a LinkedIn outreach playbook on the
user's own machine: persistent Chrome profile, SQLite state, YAML-defined sequences
(`linkedin_agent/campaigns/`), caps/ramp/governor/breaker, user-written message
templates, model-written comments, a CLI (`linkedin_agent/cli.py`), a local web UI
(`linkedin_agent/ui/`) and an MCP server (`linkedin_agent/mcp_server.py`) over a shared
`service.py`. `docs/developers.md` is the map; read it first.

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q          # tests fake the browser and the LLM (tests/conftest.py)
ruff check . && ruff format --check .
mypy linkedin_agent          # strict
```

Use `python -m pytest`, not a bare `pytest`, so the venv's install is the one that runs.
All three checks must pass before a push; CI runs the same three.

## Non-obvious patterns

- `browser-use` is pinned to 0.11.3 and `tests/test_browser_use_surface.py` guards the
  exact API surface the pool and executor use. Bumping the pin means updating that test.
- Every browser action is a prompt in `linkedin_agent/core/tasks/` that returns JSON only.
  The runner routes on `status` strings; `core/status_map.py` is the single table of
  which statuses mean success, soft skip, or cannot-contact. Add statuses in both places
  and in `.claude/skills/linkedin-campaign/SKILL.md`.
- Lead stages only move forward (`_advance_stage`). `scheduler.restart_lead` is the one
  place that lowers a stage on purpose.
- Three verdicts are verified before they are believed, each because it misfired in a
  real run: `cannot_connect` (read-only re-check), `login_required` (feed page load),
  `replied` (position relative to our message plus `lead.prior_reply_text`). See
  `core/runner.py` and `core/status_map.normalize_reply_check`.
- A retried message checks the thread first (`_already_sent`) so a lost confirmation
  never becomes a duplicate send.
- `process_task` builds the prompt before touching the browser: a task whose parameters
  can never be phrased (an invalid post URL, missing text) is a soft skip, not a retry,
  so a scrape glitch cannot trip the breaker. Everything the browser model reports is
  cleaned before it is stored: `status_map.clean_profile` (text limits, degree,
  mutual count, company URL) and `clean_post_url` (post links from visits and from
  like/comment results). Add new model-returned fields there, never store them raw.
- `run_loop` sleeps through `nap`, which detects a wall-clock jump (the machine slept),
  marks the browser dead and waits for LinkedIn to answer (`_after_wake`). Crash-kind
  failures give the attempt back and use `_crash_retries` (max 6) instead.
- `browser_pool.ensure_tab` runs before every task: browser-use 0.11 turns navigation
  into a silent no-op when the session has no focused tab.
- `run_loop` is built to stay up for weeks: exceptions in a tick or an iteration are
  logged and survived (`MAX_LOOP_ERRORS` in a row stops it), a suspended machine is
  detected by wall-vs-monotonic drift (`SleepWatch`), and session expiry waits for
  `login` instead of exiting. `scheduler.refresh_campaigns` reloads edited campaign
  files each tick; `tick` isolates a lead whose step id no longer exists.
- Tasks record the pid that claimed them (schema v3, `claimed_by`);
  `requeue_stale_running` releases tasks of dead processes at once and leaves live ones
  alone. `run` refuses to start while the heartbeat shows another loop; `stop` signals it.
- Login uses a plain, un-automated Chrome subprocess on the profile dir; the same binary
  must run tasks because Chrome encrypts cookies per binary.
- Schema: `adapters/sqlite/schema.sql` for fresh databases plus `MIGRATIONS` in
  `adapters/sqlite/db.py` for existing ones. Bump `SCHEMA_VERSION` together.
- Numbers shown to the user live in `linkedin_agent/reporting.py`; operations live in
  `linkedin_agent/service.py`. The CLI, the UI and the MCP tools call those; never
  compute a metric or implement an operation in `cli.py`, the page, or a tool body.
- The MCP server runs over stdio: nothing may print to stdout while it serves.
- Send windows live in `core/timezone.py` `WINDOWS` (`send`, `engage`, `any`). A campaign
  may add or override them with a `windows:` block; `Campaign.window_specs` is what the
  scheduler passes down, and the `Campaign` validator is what rejects a step naming a
  window nothing defines (`Window` is a plain `str`, not a Literal, for that reason).
- `LINKEDIN_AGENT_FAST_TEST=true` drops windows, spacing and pacing for end-to-end
  tests. Caps and ramp still apply.
- Default models are `google/gemini-2.5-flash` for both browser and text through
  OpenRouter. `LINKEDIN_AGENT_LLM_PROVIDER=ollama` switches both to a local Ollama
  (`llm.py` builds either client; `settings.llm_ready` is the gate, not the key).

## Skills

- `skills/linkedin-agent/SKILL.md`: the portable entry point users install globally; it
  clones this repository and defers to the three skills below. Keep its table and rules
  in step with them.
- `.claude/skills/linkedin-setup/SKILL.md`: how to install and configure the agent for a
  new user through the shell (`init --skip-key`, `login --open` / `--verify`, `doctor`).
- `.claude/skills/linkedin-outreach/SKILL.md`: how to operate the agent through the MCP
  tools (triage order, confirmation rules, importing, testing). Keep it in sync with the
  tools in `mcp_server.py` and the server `INSTRUCTIONS`.
- `.claude/skills/linkedin-campaign/SKILL.md`: how to write, review or debug a campaign
  YAML. Load it whenever a task touches `campaigns/*.yaml`, message templates, step
  routing or `campaign check` errors. It mirrors the validator; keep it in sync when
  `Campaign`, `SequenceStep`, the action statuses or `campaign_check` change.

## Docs

`README.md` is the front door. `docs/` holds getting-started, campaigns, daily-use,
safety, troubleshooting, developers, design and research. When behaviour changes, the
matching page changes in the same commit. Write for someone who has never seen the
code: short sentences, one idea each, commands in code blocks.

## Style

Ruff, line length 100, Python 3.11+, mypy strict, all I/O async. Tests are
non-negotiable: every runner branch, prompt outcome, and CLI command has one.
