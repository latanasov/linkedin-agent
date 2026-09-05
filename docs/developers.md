# For developers

How the code is organised, how to run the checks, and how to change things safely.

## Run the checks

```bash
pip install -e ".[dev]"
python -m pytest -q          # fakes the browser and the model; runs in a few seconds
ruff check . && ruff format --check .
mypy linkedin_agent          # strict
```

All three must pass before a push.

**Enable CI once.** The workflow that runs these three checks on every push and pull
request is in `ci/github-workflow.yml`. Creating files under `.github/workflows/` needs
a token with the `workflow` scope, which the tool that created this repository did not
have, so copy it in from your own machine:

```bash
mkdir -p .github/workflows && cp ci/github-workflow.yml .github/workflows/ci.yml
git add .github && git commit -m "ci: enable" && git push
```

## Layout

```
linkedin_agent/
  cli.py               every command; thin, delegates to the modules below
  config.py            Settings (pydantic-settings, prefix LINKEDIN_AGENT_, ~/.linkedin-agent/.env)
  models.py            the domain: Action, LeadStage, LeadRecord, Campaign, SequenceStep, Task, TaskResult
  ports.py             Protocols the core depends on: TaskQueue, LeadStore, ActionLog, AccountStore,
                       ReviewQueue, TextLLM, TaskExecutor, BrowserProvider
  bootstrap.py         builds a Deps object from Settings (SQLite adapters, browser pool, LLMs)
  scheduler.py         turns due sequence steps into tasks; retry/skip/restart; review decisions
  reporting.py         read-only views shared by the CLI, the web UI and the MCP server
  service.py           every operation a front end can ask for; the MCP tools wrap it; run heartbeat
  mcp_server.py        the MCP server (stdio): one tool per Service operation, plus instructions
  llm.py               OpenRouter clients (text via httpx; browser via browser-use's ChatOpenRouter)
  core/
    runner.py          process_task: gates, content, browser, execute, persist, advance
    sequence.py        the sequence engine: branches, routing, jitter, task materialisation
    status_map.py      which result statuses mean success / soft skip / cannot contact;
                       stage transitions; stale-reply normalisation
    limits.py          caps, tier ceilings, ramp, spacing, governor
    timezone.py        built-in and campaign-defined windows in the lead's local time; location → zone guess
    messages.py        template rendering, hook and comment drafting and checks, campaign_check
    errors.py          classify exceptions and results: crash / session expired / restricted / other
    prompts.py         URL validation, text sanitisation, running a browser-use Agent
    browser_pool.py    one Chromium per account on a persistent profile; tab health; login helpers
    tasks/             one prompt builder per action, each returning JSON-only instructions
  adapters/
    sqlite/            schema.sql, migrations, and one store per port
    browser_use_executor.py
    csv_import.py
  campaigns/           default.yaml, inmail.yaml, cold-minimal.yaml, three-week.yaml
  ui/                  FastAPI app + one static HTML page
tests/                 conftest.py fakes the executor, pool and LLM; NOW is a fixed Wednesday
docs/
.claude/skills/linkedin-campaign/   a Claude Code skill: the campaign-file reference
```

## How a task flows

1. `scheduler.tick` finds sequences whose `next_due_at` has passed, skips steps that do
   not apply to the lead's branch, checks caps and spacing, and enqueues a `Task` with
   `not_before`/`not_after` from the step's window.
2. `runner.run_loop` claims the next runnable task and calls `process_task`.
3. `process_task` checks the account gates (session, breaker, caps), renders the content
   (`messages.render_message`, `draft_comment`), guards against identical copy, runs the
   pre-send reply check for messages, gets a browser from the pool, and executes.
4. The executor builds the prompt for the action (`core/tasks/<action>.py`) and runs a
   browser-use `Agent`. The result is parsed into a `TaskResult` with a `status` string.
5. `status_map.apply_result` updates the lead (stage, timestamps, scraped profile), and
   `sequence.advance` routes to the next step from `on_result` or the defaults.
6. Failures go through `_fail`: session expiry stops the loop, restriction trips the
   breaker, three plain failures trip it too, crashes restart the browser, and retryable
   tasks are requeued ten minutes later, up to three attempts.

## Things that are not obvious

- **Every action is a prompt that returns JSON only.** The runner routes on the
  `status` string. `core/status_map.py` is the single table of what each status means.
  Add a new status there and in the prompt, and document it in the skill.
- **Stages only move forward.** `scheduler.restart_lead` is the one place that lowers a
  stage, on purpose.
- **A `cannot_connect` is verified** by a read-only check before it ends a lead.
  A `login_required` is verified by loading the feed. A `replied` is normalised against
  position and the prior reply text. These exist because each came up in a real run.
- **A retried message checks the thread first** (`_already_sent`) so a lost
  confirmation never becomes a duplicate send.
- **The browser pool requires a focused tab** before every task (`ensure_tab`). Without
  one, browser-use turns navigation into a silent no-op and the model sees an empty page.
- **Login uses a plain Chrome subprocess** with no automation attached, because
  browser-use attaches to popups and freezes SSO. The same binary must run tasks:
  Chrome encrypts profile cookies per binary.
- **browser-use is pinned** to 0.11.3. `tests/test_browser_use_surface.py` fails
  loudly if a bump removes anything the pool or executor relies on. Bump the pin, fix
  that test, then run the pool against a real browser once.
- **Schema changes** go in `adapters/sqlite/schema.sql` for fresh databases and as an
  entry in `MIGRATIONS` in `adapters/sqlite/db.py` for existing ones. Bump
  `SCHEMA_VERSION`. `tests/test_sqlite.py` upgrades a version-1 database.
- **Numbers live in `reporting.py`**, used by both the CLI and the UI. Do not compute a
  metric in `cli.py` or in the page.
- **Nothing the browser model reports is stored raw.** `status_map.clean_profile` and
  `clean_post_url` whitelist, truncate and validate every visit field and every post URL;
  `process_task` builds the prompt before the browser so a task with parameters that can
  never be phrased is a soft skip, not three retries and a tripped breaker.
- **Fast-test mode** drops windows, spacing and pacing; caps and ramp stay.
- **The MCP server never opens a browser.** One-off actions become queued `Task`s for the
  CLI run loop, which writes `~/.linkedin-agent/run.json` every 20 s so `run_state` can
  tell whether anything will execute them. Stdout is the MCP channel: never print there.
- **Operations live in `service.py`.** A new capability goes there first, then gets a
  tool in `mcp_server.py`, a CLI command if useful, and tests in `test_service.py` and
  `test_mcp.py`.

## Adding an action

1. Add it to `Action` in `models.py`, and to `TOUCH_ACTIONS` or `READ_ONLY_ACTIONS`.
2. Write `core/tasks/<action>.py` with a `build_prompt(profile_url, params)` returning
   JSON-only instructions, and register it in `core/tasks/__init__.py`.
3. Add its cap to `BASE_CAPS` in `core/limits.py` and its statuses to the tables in
   `core/status_map.py`; add stage side effects in `apply_result` if any.
4. Give it a step budget in `adapters/browser_use_executor.py` `MAX_STEPS`.
5. Add a one-off CLI command and, if it belongs in the playbook, a step in
   `campaigns/default.yaml`.
6. Tests: the prompt (`test_prompts.py`), the status table (`test_status_map.py`), a
   runner path (`test_runner.py`), and the campaign still validating (`test_campaigns.py`).
7. Document it in `docs/campaigns.md` and in the skill.

## Testing against a real browser

The suite never opens a browser. When you change `browser_pool.py`, the executor or a
prompt, run one real action from the CLI with `-v` and read the browser-use step log:

```bash
linkedin-agent -v check https://www.linkedin.com/in/<someone>/
```

The web UI has no browser test either. After changing `ui/static/index.html`, start
`linkedin-agent ui` against a seeded database and click through every tab and one action.

## Where things came from

The sequence, timings and thresholds come from [research.md](research.md). The
architecture and the reasoning behind each choice are in [design.md](design.md).
