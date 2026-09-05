"""linkedin-agent command line."""

from __future__ import annotations

import asyncio
import csv
import logging
import os
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn, TypeVar

import typer

from . import __version__, reporting
from .bootstrap import App, build_app
from .campaigns import (
    CampaignError,
    builtin_campaigns,
    find_campaign,
    load_all_user_campaigns,
    load_campaign,
    new_campaign_file,
    resolve_campaign_path,
)
from .config import Settings, load_settings
from .core import messages as msg
from .core import sequence as seqeng
from .core.browser_pool import (
    LOGIN_URL,
    launch_plain_chrome,
    resolve_chrome_executable,
    seed_li_at_cookie,
    stop_plain_chrome,
    user_agent_of,
    verify_logged_in,
)
from .core.limits import ramp_week
from .core.proc import pid_alive
from .core.prompts import LINKEDIN_URL_RE
from .core.runner import process_task, run_loop
from .models import Action, LeadStage, Task
from .scheduler import resolve_review, restart_lead, retry_lead, skip_lead_step, tick
from .service import (
    Service,
    ServiceError,
    clear_heartbeat,
    format_import,
    run_state,
    write_heartbeat,
)

logger = logging.getLogger(__name__)

app = typer.Typer(help="Standalone local LinkedIn outreach agent.", no_args_is_help=True)
campaign_app = typer.Typer(help="Manage campaign files.", no_args_is_help=True)
breaker_app = typer.Typer(help="Circuit breaker.", no_args_is_help=True)
app.add_typer(campaign_app, name="campaign")
app.add_typer(breaker_app, name="breaker")

T = TypeVar("T")
HEARTBEAT_EVERY_S = 20


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _settings() -> Settings:
    return load_settings()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _echo(text: str = "") -> None:
    typer.echo(text)


def _fail(text: str, code: int = 1) -> NoReturn:
    typer.secho(text, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


async def _with_app(fn: Callable[[App], Awaitable[T]], *, need_llm: bool = True) -> T:
    settings = _settings()
    app_ = await build_app(settings, need_llm=need_llm)
    try:
        return await fn(app_)
    finally:
        await app_.close()


# ── setup ─────────────────────────────────────────────────────────────────


@app.callback()
def _main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging")) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Chatty libraries that never help with a LinkedIn problem, even in verbose mode.
    for name in ("aiosqlite", "asyncio", "httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
    if verbose:
        for name in ("browser_use", "cdp_use", "bubus"):
            logging.getLogger(name).setLevel(logging.INFO)
        os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "info")
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
    else:
        # browser-use logs every transient snapshot hiccup as a WARNING/ERROR with a
        # traceback; it recovers on its own. Only show those with -v.
        for name in ("browser_use", "cdp_use", "bubus", "asyncio"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
        os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "critical")
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")


@app.command()
def version() -> None:
    _echo(f"linkedin-agent {__version__}")


@app.command()
def init(
    openrouter_api_key: str = typer.Option(None, "--openrouter-api-key", hide_input=True),
    timezone_name: str = typer.Option(
        "UTC", "--timezone", prompt="Your time zone (IANA, e.g. Europe/Sofia)"
    ),
    tier: str = typer.Option("pro", prompt="Cap tier (free/pro/ultimate)"),
    chrome_path: str = typer.Option(
        "", prompt="Path to Chrome executable (empty = Playwright Chromium)"
    ),
    skip_key: bool = typer.Option(
        False, "--skip-key", help="Write the file without the key; paste it in afterwards"
    ),
) -> None:
    """Create ~/.linkedin-agent and write the .env file.

    Pass `--skip-key` when someone else (an assistant) runs this for you: the key is
    then the one thing you add by hand, so it never passes through a chat."""
    settings = _settings()
    if openrouter_api_key is None and not skip_key:
        openrouter_api_key = typer.prompt("OpenRouter API key", hide_input=True)
    settings.home.mkdir(parents=True, exist_ok=True)
    settings.campaigns_dir.mkdir(parents=True, exist_ok=True)
    env = settings.home / ".env"
    lines = [
        f"LINKEDIN_AGENT_OPENROUTER_API_KEY={openrouter_api_key or ''}",
        f"LINKEDIN_AGENT_DEFAULT_TIMEZONE={timezone_name}",
        f"LINKEDIN_AGENT_TIER={tier}",
    ]
    if chrome_path:
        lines.append(f"LINKEDIN_AGENT_CHROME_PATH={chrome_path}")
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        env.chmod(0o600)
    except OSError:
        pass
    _echo(f"Wrote {env}")
    if not openrouter_api_key:
        _echo(f"Paste your OpenRouter key into {env} after LINKEDIN_AGENT_OPENROUTER_API_KEY=")
    if not chrome_path:
        _echo("If you have not yet: playwright install chromium")
    _echo("Next: linkedin-agent login")


@app.command()
def login(
    account: str = typer.Option(None, help="Profile name (default from settings)"),
    cookie: str = typer.Option(
        None, "--cookie", help="li_at cookie value (fallback for headless machines)"
    ),
    open_only: bool = typer.Option(
        False, "--open", help="Open the Chrome window and return; verify later with --verify"
    ),
    verify_only: bool = typer.Option(
        False, "--verify", help="Check the saved profile is logged in (after --open)"
    ),
) -> None:
    """Open a plain Chrome window, sign in once, keep the profile.

    Interactive by default. `--open` then `--verify` split it into two commands so an
    assistant can drive the setup while you do the signing in."""

    async def go(app_: App) -> None:
        name = account or app_.settings.account
        pool = app_.deps.pool
        try:
            exe = await resolve_chrome_executable(app_.settings)
        except (FileNotFoundError, RuntimeError) as e:
            _fail(str(e))
        profile_dir = app_.settings.profile_dir(name)
        if cookie:
            browser = await pool.open(name, headless=True)  # type: ignore[attr-defined]
            await browser.navigate_to("https://www.linkedin.com/")
            await seed_li_at_cookie(browser, cookie.strip())
        elif verify_only:
            browser = await pool.open(name, headless=True)  # type: ignore[attr-defined]
        else:
            # A normal Chrome process on the agent's profile: no automation attached, so
            # Google sign-in popups and 2FA work exactly as in your everyday browser.
            proc = launch_plain_chrome(exe, profile_dir, LOGIN_URL)
            _echo(f"Chrome opened on {profile_dir} using {exe}.")
            _echo("Sign in to LinkedIn (Google sign-in and 2FA work normally).")
            if open_only:
                _echo(
                    "When you can see your feed, close that Chrome window, then run "
                    "`linkedin-agent login --verify`."
                )
                return
            _echo("When you can see your feed, close that Chrome window, then come back here.")
            typer.prompt("Press Enter to continue", default="", show_default=False)
            stop_plain_chrome(proc)
            await asyncio.sleep(2)  # let Chrome flush the cookie store
            browser = await pool.open(name, headless=True)  # type: ignore[attr-defined]
        ok, url = await verify_logged_in(browser)
        if not ok:
            _fail(
                f"Still not logged in (landed on {url}). Run `linkedin-agent login` again "
                "and make sure the feed loaded before closing the window."
            )
        acct = await app_.deps.accounts.get(name)
        acct.logged_in_at = _now()
        acct.session_expired_at = None
        acct.user_agent = await user_agent_of(browser)
        await app_.deps.accounts.save(acct)
        _echo(f"Logged in. Profile saved under {profile_dir}")

    _run(_with_app(go, need_llm=False))


@app.command()
def doctor(account: str = typer.Option(None)) -> None:
    """Check the setup end to end and say what is missing. Exit code 1 if anything fails."""
    from .service import run_state

    async def go(app_: App) -> None:
        settings = app_.settings
        name = account or settings.account
        failures = 0

        def line(ok: bool, what: str, fix: str = "") -> None:
            nonlocal failures
            failures += 0 if ok else 1
            mark = (
                typer.style("ok  ", fg=typer.colors.GREEN)
                if ok
                else typer.style("FAIL", fg=typer.colors.RED)
            )
            _echo(f"{mark} {what}" + (f"  → {fix}" if not ok and fix else ""))

        line(sys.version_info >= (3, 11), f"python {sys.version.split()[0]}", "install 3.11+")
        env = settings.home / ".env"
        line(env.exists(), f"settings file {env}", "run `linkedin-agent init`")
        roles = (
            ("browser", settings.browser_provider, settings.browser_llm_model),
            ("text", settings.text_provider, settings.text_llm_model),
        )
        for which, provider, model in roles:
            line(True, f"{which} model {model} via {provider}")
        if settings.uses_openrouter:
            line(
                bool(settings.openrouter_api_key),
                "OpenRouter API key set",
                f"paste it into {env} after LINKEDIN_AGENT_OPENROUTER_API_KEY=",
            )
        local = [(which, model) for which, provider, model in roles if provider == "ollama"]
        if local:
            from .llm import ollama_models

            models = await ollama_models(settings.ollama_host)
            line(
                models is not None,
                f"Ollama at {settings.ollama_host}",
                "start Ollama (`ollama serve`)",
            )
            for which, model in local:
                if models is not None:
                    have = any(m == model or m.split(":")[0] == model.split(":")[0] for m in models)
                    line(have, f"Ollama {which} model {model}", f"`ollama pull {model}`")
        try:
            exe = await resolve_chrome_executable(settings)
            line(True, f"browser {exe}")
        except (FileNotFoundError, RuntimeError) as e:
            line(False, "browser", f"{e}")
        acct = await app_.deps.accounts.get(name)
        profile = settings.profile_dir(name)
        if acct.session_expired_at:
            line(False, "LinkedIn login", "session expired: run `linkedin-agent login`")
        elif acct.logged_in_at:
            line(True, f"LinkedIn login verified {acct.logged_in_at:%Y-%m-%d}")
        else:
            line(
                False,
                "LinkedIn login",
                "run `linkedin-agent login` (or `login --open`, sign in, `login --verify`)",
            )
        line(profile.exists(), f"profile dir {profile}", "created by `login`")
        camps = app_.deps.campaigns
        if camps:
            line(True, f"campaigns: {', '.join(sorted(camps))}")
        else:
            _echo("note campaigns: none (fine for one-off visits and messages; sequences need one)")
        for cname, camp in sorted(camps.items()):
            errors, warnings = msg.campaign_check(camp)
            line(
                not errors,
                f"campaign {cname} valid" + (f" ({len(warnings)} warning(s))" if warnings else ""),
                "; ".join(errors),
            )
        stages = await app_.deps.leads.stage_counts()
        total = sum(stages.values())
        if camps:
            line(total > 0, f"leads: {total}", "run `import <csv> --campaign <name>`")
        else:
            _echo(f"note leads: {total}")
        if acct.tripped_until and acct.tripped_until > _now():
            line(False, "circuit breaker", f"tripped until {acct.tripped_until:%Y-%m-%d %H:%M}")
        else:
            line(True, "circuit breaker ok")
        rs = run_state(settings, _now())
        _echo(
            ("run loop active" if rs["active"] else "run loop not active")
            + ("" if rs["active"] else " (start `linkedin-agent run` when ready)")
        )
        if failures:
            _fail(f"{failures} check(s) need attention.")
        _echo("All checks passed.")

    _run(_with_app(go, need_llm=False))


@app.command()
def logout(account: str = typer.Option(None)) -> None:
    """Delete the saved Chrome profile for an account."""
    import shutil

    settings = _settings()
    name = account or settings.account
    path = settings.profile_dir(name)
    if not path.exists():
        _fail(f"No profile at {path}")
    if not typer.confirm(f"Delete {path}?"):
        raise typer.Exit()
    shutil.rmtree(path)
    _echo("Profile deleted.")


# ── campaigns ─────────────────────────────────────────────────────────────


@campaign_app.command("new")
def campaign_new(
    name: str, template: str = typer.Option("default", help="Built-in to copy")
) -> None:
    """Copy a built-in campaign into ~/.linkedin-agent/campaigns/<name>.yaml."""
    try:
        path = new_campaign_file(name, _settings(), template)
    except CampaignError as e:
        _fail(str(e))
    _echo(f"Created {path}. Edit the top block and the messages, then `campaign check {name}`.")


@campaign_app.command("list")
def campaign_list() -> None:
    settings = _settings()
    _echo("Built-in:")
    for p in builtin_campaigns():
        _echo(f"  {p.stem}")
    user = load_all_user_campaigns(settings)
    _echo(f"Yours ({settings.campaigns_dir}):")
    for name in user:
        _echo(f"  {name}")


@campaign_app.command("check")
def campaign_check(name: str) -> None:
    """Validate a campaign file: steps, placeholders, template lengths."""
    settings = _settings()
    try:
        path = resolve_campaign_path(name, settings)
        campaign = load_campaign(path)
    except CampaignError as e:
        _fail(str(e))
    errors, warnings = msg.campaign_check(campaign)
    for w in warnings:
        typer.secho(f"warning: {w}", fg=typer.colors.YELLOW)
    for err in errors:
        typer.secho(f"error: {err}", fg=typer.colors.RED)
    if errors:
        raise typer.Exit(1)
    _echo(f"{path}: OK ({len(campaign.steps)} steps, {len(campaign.messages)} messages)")


@campaign_app.command("show")
def campaign_show(name: str) -> None:
    settings = _settings()
    try:
        c = find_campaign(name, settings)
    except CampaignError as e:
        _fail(str(e))
    _echo(
        f"{c.name}  mode={c.mode}  review_comments={c.review_comments}  "
        f"hook={c.personalization.hook}"
    )
    for s in c.steps:
        br = "" if s.branch == "any" else f" [{s.branch}]"
        _echo(f"  {s.id:<14} {s.action.value:<16} after {s.after:<4} window {s.window}{br}")
    from .core.timezone import WINDOWS, describe_window

    specs = c.window_specs
    used = sorted({s.window for s in c.steps})
    _echo("windows (in each person's local time):")
    for w in used:
        spec = specs.get(w) or WINDOWS[w]
        own = " (this campaign)" if w in specs else ""
        _echo(f"  {w:<14} {describe_window(spec)}{own}")


# ── leads ─────────────────────────────────────────────────────────────────


@app.command("import")
def import_leads(
    csv_path: Path = typer.Argument(..., exists=True, readable=True),
    campaign: str = typer.Option(..., "--campaign", "-c", help="Campaign name or YAML path"),
) -> None:
    """Load leads from a CSV and start their sequences."""
    from .adapters.csv_import import parse_leads

    async def go(app_: App) -> None:
        try:
            camp = find_campaign(campaign, app_.settings)
        except CampaignError as e:
            _fail(str(e))
        try:
            result = parse_leads(csv_path, camp.name, camp.default_timezone)
            summary = await Service(app_.deps, app_.settings).import_leads(result, camp)
        except (ValueError, ServiceError) as e:
            _fail(str(e))
        for w in summary.warnings:
            typer.secho(f"warning: {w}", fg=typer.colors.YELLOW)
        _echo(format_import(summary))

    _run(_with_app(go, need_llm=False))


@app.command()
def preview(lead: str = typer.Argument(..., help="Lead slug, URL, id or full name")) -> None:
    """Render every message for one lead exactly as it would be sent."""

    async def go(app_: App) -> None:
        rec = await app_.deps.leads.find(lead)
        if rec is None:
            _fail(f"No lead matches {lead!r}")
        camp = app_.deps.campaigns.get(rec.campaign)
        if camp is None:
            _fail(f"Campaign {rec.campaign!r} not found in {app_.settings.campaigns_dir}")
        _echo(f"{rec.display_name} · {rec.company or '-'} · {rec.title or '-'} · tz {rec.timezone}")
        if not rec.posts:
            _echo(
                "(no profile data yet — placeholders use CSV values; the visit step fills the rest)"
            )
        for name in camp.messages:
            r = await msg.render_message(name, rec, camp, app_.deps.text_llm)
            tag = (
                " (hook: model)"
                if r.hook_used
                else (" (hook: fallback)" if r.hook_fallback_used else "")
            )
            _echo(f"\n[{name}]{tag}")
            _echo(r.text if r.text else "(blank)")
            for w in r.warnings:
                typer.secho(f"  ! {w}", fg=typer.colors.YELLOW)

    _run(_with_app(go))


# ── run ───────────────────────────────────────────────────────────────────


@app.command()
def run(
    account: str = typer.Option(None),
    once: bool = typer.Option(
        False, "--once", help="One scheduler tick, drain what is due now, exit"
    ),
    headless: bool = typer.Option(
        None, "--headless/--headed", help="Override LINKEDIN_AGENT_HEADLESS"
    ),
    max_tasks: int = typer.Option(None, help="Stop after N tasks"),
) -> None:
    """Schedule due steps and execute them with human pacing. Ctrl-C to stop."""

    async def go(app_: App) -> None:
        if headless is not None:
            app_.settings.headless = headless
        name = account or app_.settings.account
        deps = app_.deps
        if not deps.campaigns:
            _echo(
                "No campaign files loaded: only one-off actions you queue (CLI commands, the "
                "dashboard, or `enqueue_action` from an assistant) will run."
            )
        if not app_.settings.llm_ready:
            _fail("LINKEDIN_AGENT_OPENROUTER_API_KEY is not set (run `init`).")
        local = [
            f"{which} {model}"
            for which, provider, model in (
                ("browser", app_.settings.browser_provider, app_.settings.browser_llm_model),
                ("text", app_.settings.text_provider, app_.settings.text_llm_model),
            )
            if provider == "ollama"
        ]
        if local:
            _echo(
                f"Local models via Ollama at {app_.settings.ollama_host}: " + ", ".join(local) + "."
            )
        if app_.settings.fast_test:
            typer.secho(
                "FAST TEST MODE: send windows and per-prospect spacing are OFF. "
                "Use only with a small test list.",
                fg=typer.colors.YELLOW,
            )
        state = run_state(app_.settings, _now())
        if state["active"] and state.get("pid") != os.getpid():
            _fail(
                f"A run loop is already active (pid {state['pid']}, since "
                f"{str(state.get('started_at') or '')[:16].replace('T', ' ')}). Two loops on "
                "one profile means two Chromes on one LinkedIn session. Stop it first: "
                "`linkedin-agent stop`."
            )
        acct = await deps.accounts.get(name)
        if acct.first_action_at is None:
            _echo("New account: week-1 ramp (25% of caps) is active.")
        else:
            _echo(
                f"Ramp week {ramp_week((_now() - acct.first_action_at).days)} · "
                f"governor {acct.governor_state.value}"
            )

        async def do_tick() -> None:
            rep = await tick(deps, name)
            stamp = datetime.now().strftime("%H:%M")
            _echo(f"{stamp}  tick      {rep.summary()}")
            for n in rep.notes:
                _echo(f"         note      {n}")

        def emit(line: str) -> None:
            _echo(f"{datetime.now().strftime('%H:%M')}  {line}")

        started = _now()

        async def heartbeat() -> None:
            while True:
                try:
                    write_heartbeat(app_.settings, name, started)
                except OSError as e:  # a full disk must not silently end the heartbeat
                    logger.warning("heartbeat not written: %s", e)
                await asyncio.sleep(HEARTBEAT_EVERY_S)

        # `kill <pid>` and Ctrl-C both land here, so the browser is closed, the heartbeat
        # cleared and the claimed task released, instead of Chrome being left behind.
        main = asyncio.current_task()
        loop = asyncio.get_running_loop()
        installed: list[signal.Signals] = []

        def _stop(sig: signal.Signals) -> None:
            emit(f"received {sig.name}; stopping")
            if main is not None:
                main.cancel()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _stop, sig)
                installed.append(sig)
            except (NotImplementedError, RuntimeError):  # Windows, or no event loop signals
                pass

        beat = asyncio.ensure_future(heartbeat())
        n = 0
        try:
            n = await run_loop(
                deps, name, once=once, on_event=emit, tick=do_tick, max_tasks=max_tasks
            )
        except asyncio.CancelledError:
            _echo("Stopped.")
        finally:
            beat.cancel()
            for sig in installed:
                loop.remove_signal_handler(sig)
            clear_heartbeat(app_.settings)
        _echo(f"Processed {n} task(s).")

    try:
        _run(_with_app(go))
    except KeyboardInterrupt:
        _echo("\nStopped.")


@app.command()
def stop(timeout: float = typer.Option(30.0, help="Seconds to wait for it to exit")) -> None:
    """Stop the run loop started from any terminal, cleanly: browser closed, task released."""
    settings = _settings()
    state = run_state(settings, _now())
    pid = state.get("pid")
    if not state["active"] or not pid:
        _echo("No run loop is active." + (f" ({state['reason']})" if state.get("reason") else ""))
        return
    try:
        os.kill(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        clear_heartbeat(settings)
        _echo(f"Process {pid} was already gone; cleared the stale heartbeat.")
        return
    _echo(f"Asked pid {pid} to stop…")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(int(pid)):
            _echo("Stopped.")
            return
        time.sleep(0.5)
    _fail(f"Still running after {timeout:.0f}s. `kill -9 {pid}` if it is stuck.")


# ── review / inbox / status / report ──────────────────────────────────────


@app.command()
def review(list_only: bool = typer.Option(False, "--list")) -> None:
    """Approve, edit or reject model-drafted comments waiting for you."""

    async def go(app_: App) -> None:
        items = await app_.deps.review.pending()
        if not items:
            _echo("Nothing to review.")
            return
        for i, item in enumerate(items, 1):
            ctx = item.context
            _echo(
                f"\n[{i}/{len(items)}] {ctx.get('lead')} · "
                f"post {ctx.get('post_age_days', '?')}d ago"
            )
            _echo(f'     "{str(ctx.get("post_text", ""))[:200]}"')
            _echo(f"     Draft: {item.draft}")
            if list_only:
                continue
            choice = (
                typer.prompt("     [a]pprove [e]dit [r]eject [s]kip", default="s").strip().lower()
            )
            if choice == "a":
                _echo("     " + await resolve_review(app_.deps, item.task_id, item.draft, _now()))
            elif choice == "e":
                text = typer.prompt("     New text")
                _echo("     " + await resolve_review(app_.deps, item.task_id, text, _now()))
            elif choice == "r":
                _echo("     " + await resolve_review(app_.deps, item.task_id, None, _now()))

    _run(_with_app(go, need_llm=False))


@app.command()
def inbox(
    handled: str = typer.Option(None, "--handled", help="Mark a lead as handled (done)"),
) -> None:
    """Leads who replied; their sequences are stopped and waiting for you."""

    async def go(app_: App) -> None:
        if handled:
            rec = await app_.deps.leads.find(handled)
            if rec is None:
                _fail(f"No lead matches {handled!r}")
            rec.stage = LeadStage.DONE
            await app_.deps.leads.update(rec)
            _echo(f"{rec.display_name} marked done.")
            return
        rows = await reporting.inbox_rows(app_.deps, _now())
        if not rows:
            _echo("Inbox empty.")
            return
        for row in rows:
            when = (row["replied_at"] or "?")[:16].replace("T", " ")
            _echo(f"{row['name']:<28} replied {when}   {row['linkedin_url']}")

    _run(_with_app(go, need_llm=False))


@app.command()
def status(account: str = typer.Option(None)) -> None:
    """Account health, today's usage against caps, queues, last results."""

    async def go(app_: App) -> None:
        name = account or app_.settings.account
        deps = app_.deps
        now = _now()
        health = await reporting.account_health(deps, name, now)
        login_state = {
            "logged_in": f"logged in {(health['logged_in_at'] or '')[:10]}",
            "profile_exists": "profile exists",
            "not_logged_in": "NOT logged in",
            "session_expired": "SESSION EXPIRED — run `login`",
        }[health["login"]]
        breaker = "ok"
        if health["breaker_tripped"]:
            until = health["tripped_until"][:16].replace("T", " ")
            breaker = f"TRIPPED until {until} ({health['trip_reason']})"
        _echo(
            f"account {name} · {login_state} · breaker {breaker} · "
            f"governor {health['governor']} · ramp week {health['ramp_week']}"
        )
        # The pid matters: a detached run is stopped with `kill`, there being no `stop`.
        state = run_state(app_.settings, now)
        if state["active"]:
            since = str(state.get("started_at") or "")[:16].replace("T", " ")
            fast = " · FAST TEST (windows and spacing off)" if state.get("fast_test") else ""
            _echo(f"run loop: active · pid {state['pid']} · since {since}{fast}")
        else:
            _echo(
                f"run loop: NOT active ({state.get('reason', 'stopped')}) — "
                "nothing reaches LinkedIn until you start `linkedin-agent run`"
            )
        cols = []
        for u in await reporting.usage_today(deps, name, now):
            if u["action"] in ("check_connection", "check_replies"):
                continue
            col = f"{u['action']} {u['day']}/{u['day_cap']}"
            if u["week_cap"] is not None:
                col += f" (week {u['week']}/{u['week_cap']})"
            cols.append(col)
        _echo("today: " + " · ".join(cols))
        q = await reporting.queue_summary(deps, name)
        _echo(
            f"queue: {q['queued']} queued · {q['running']} running · "
            f"{q['awaiting_review']} awaiting review · {q['done']} done · "
            f"{q['failed']} failed · {q['skipped']} skipped"
        )
        _echo(f"review: {q['review_pending']} · inbox: {q['inbox']}")
        stages = await deps.leads.stage_counts()
        if stages:
            _echo("leads: " + " · ".join(f"{k} {v}" for k, v in sorted(stages.items())))
        recent = await deps.queue.recent(10)
        if recent:
            _echo("last results:")
            for t in recent:
                res = (t.result or {}).get("status", t.status.value)
                who = t.params.get("lead_name") or t.profile_url
                when = t.finished_at.strftime("%m-%d %H:%M") if t.finished_at else ""
                _echo(f"  {when}  {t.action.value:<16} {str(who):<28} {res}")

    _run(_with_app(go, need_llm=False))


@app.command()
def report(
    campaign: str = typer.Option(None, "--campaign", "-c"),
    since: str = typer.Option("30d", help="Window, e.g. 14d"),
    csv_out: Path = typer.Option(None, "--csv", help="Write per-lead rows to a CSV"),
) -> None:
    """Acceptance and reply rates against the benchmarks."""
    from .models import parse_duration

    async def go(app_: App) -> None:
        r = await reporting.campaign_report(
            app_.deps, app_.settings.account, campaign, parse_duration(since), _now()
        )

        def pct(v: float | None) -> str:
            return f"{v:.1%}" if v is not None else "n/a"

        median = f"{r['median_days_to_accept']:.1f} days" if r["median_days_to_accept"] else "n/a"
        _echo(f"campaign {campaign or 'all'} · {r['leads']} leads · last {since}")
        _echo(f"warm-up completed   {r['warmed']}")
        _echo(
            f"invites sent        {r['invited']}   accepted {r['accepted']} "
            f"({pct(r['acceptance_rate'])}) · benchmark {r['acceptance_benchmark']:.1%} · "
            f"median time to accept {median}"
        )
        _echo(f"withdrawn           {r['withdrawn']}")
        _echo(
            f"messaged            {r['messaged']}   replied {r['replied']} "
            f"({pct(r['reply_rate'])}) · benchmark {r['reply_benchmark']:.1%}"
        )
        _echo("stages: " + " · ".join(f"{k} {v}" for k, v in sorted(r["stages"].items())))
        _echo(
            f"account: governor {r['governor']} · breaker "
            f"{'tripped' if r['breaker_tripped'] else 'ok'}"
        )
        if csv_out:
            cols = (
                "name",
                "linkedin_url",
                "campaign",
                "stage",
                "invited_at",
                "connected_at",
                "last_message_at",
                "replied_at",
            )
            with csv_out.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["name", "url", *cols[2:]])
                for row in r["rows"]:
                    w.writerow([row[c] for c in cols])
            _echo(f"wrote {csv_out}")

    _run(_with_app(go, need_llm=False))


@app.command()
def log(
    comments: bool = typer.Option(False, "--comments", help="Show posted comments"),
    limit: int = typer.Option(30),
) -> None:
    """Recent actions (or posted comments with their text)."""

    async def go(app_: App) -> None:
        if comments:
            rows = await app_.deps.queue.raw(  # type: ignore[attr-defined]
                "SELECT finished_at, params, profile_url FROM tasks "
                "WHERE action='comment_post' AND status='done' ORDER BY finished_at DESC LIMIT ?",
                (limit,),
            )
            import json

            for r in rows:
                p = json.loads(r["params"] or "{}")
                _echo(
                    f"{(r['finished_at'] or '')[:16]}  {p.get('lead_name', r['profile_url'])}\n"
                    f"    {p.get('text', '')}"
                )
            if not rows:
                _echo("No comments posted yet.")
            return
        for e in await app_.deps.log.recent(app_.settings.account, None, limit):
            _echo(f"{e['at'][:16]}  {e['action']:<16} ok={e['ok']} {e['result_status'] or ''}")

    _run(_with_app(go, need_llm=False))


# ── control ───────────────────────────────────────────────────────────────


@breaker_app.command("reset")
def breaker_reset(account: str = typer.Option(None)) -> None:
    async def go(app_: App) -> None:
        acct = await app_.deps.accounts.get(account or app_.settings.account)
        acct.tripped_until, acct.trip_reason, acct.consecutive_failures = None, None, 0
        await app_.deps.accounts.save(acct)
        _echo("Circuit breaker reset.")

    _run(_with_app(go, need_llm=False))


@breaker_app.command("status")
def breaker_status(account: str = typer.Option(None)) -> None:
    async def go(app_: App) -> None:
        acct = await app_.deps.accounts.get(account or app_.settings.account)
        if acct.tripped_until and acct.tripped_until > _now():
            _echo(f"tripped until {acct.tripped_until:%Y-%m-%d %H:%M}: {acct.trip_reason}")
        else:
            _echo(f"ok (consecutive failures: {acct.consecutive_failures})")

    _run(_with_app(go, need_llm=False))


@app.command()
def retry(lead: str) -> None:
    """Re-arm a lead whose current step failed."""

    async def go(app_: App) -> None:
        rec = await app_.deps.leads.find(lead)
        if rec is None:
            _fail(f"No lead matches {lead!r}")
        _echo(await retry_lead(app_.deps, rec, _now()))

    _run(_with_app(go, need_llm=False))


@app.command()
def skip(lead: str) -> None:
    """Skip a lead's current step and move on."""

    async def go(app_: App) -> None:
        rec = await app_.deps.leads.find(lead)
        if rec is None:
            _fail(f"No lead matches {lead!r}")
        _echo(await skip_lead_step(app_.deps, rec, _now()))

    _run(_with_app(go, need_llm=False))


@app.command()
def mcp() -> None:
    """Serve the agent's tools to Claude Code, Claude Desktop, Copilot or Cursor (stdio)."""
    from .mcp_server import main

    main(_settings())


@app.command()
def ui(
    port: int = typer.Option(8765, help="Local port"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the page"),
) -> None:
    """Local web dashboard: leads, stages, queue, inbox, review, report. Localhost only."""
    from .ui import DEFAULT_HOST, serve

    async def go(app_: App) -> None:
        _echo(f"Dashboard at http://{DEFAULT_HOST}:{port}/  (Ctrl-C to stop)")
        await serve(app_, host=DEFAULT_HOST, port=port, open_browser=open_browser)

    _run(_with_app(go, need_llm=False))


@app.command()
def restart(
    lead: str,
    step: str = typer.Option(
        None, "--step", help="Step id to restart from (default: the campaign's first step)"
    ),
) -> None:
    """Put a lead back into its sequence, e.g. after a wrong 'cannot contact' verdict."""

    async def go(app_: App) -> None:
        rec = await app_.deps.leads.find(lead)
        if rec is None:
            _fail(f"No lead matches {lead!r}")
        _echo(await restart_lead(app_.deps, rec, _now(), step))

    _run(_with_app(go, need_llm=False))


@app.command()
def pause(campaign: str) -> None:
    """Freeze every lead in a campaign and cancel their queued tasks."""

    async def go(app_: App) -> None:
        n = await app_.deps.leads.pause_campaign(campaign)  # type: ignore[attr-defined]
        ids = await app_.deps.leads.lead_ids_for_campaign(campaign)  # type: ignore[attr-defined]
        c = await app_.deps.queue.cancel_open_for_leads(ids, "paused")
        _echo(f"Paused {n} sequence(s), cancelled {c} queued task(s).")

    _run(_with_app(go, need_llm=False))


@app.command()
def resume(campaign: str) -> None:
    """Resume a paused campaign; delays are recomputed from now."""

    async def go(app_: App) -> None:
        n = await app_.deps.leads.resume_campaign(campaign, _now())  # type: ignore[attr-defined]
        _echo(f"Resumed {n} sequence(s).")

    _run(_with_app(go, need_llm=False))


# ── one-off actions ───────────────────────────────────────────────────────


async def _one_off(app_: App, action: Action, url: str, params: dict[str, Any]) -> None:
    if not LINKEDIN_URL_RE.match(url):
        _fail("Not a LinkedIn profile URL")
    deps = app_.deps
    account = app_.settings.account
    lead = await deps.leads.find(url)
    task = Task(
        lead_id=lead.id if lead else None,
        action=action,
        profile_url=url,
        account=account,
        params={
            **params,
            "lead_name": lead.display_name if lead else url,
            "skip_reply_check": True,
        },
        created_at=_now(),
    )
    await deps.queue.enqueue(task)
    claimed = await deps.queue.claim(task.id, _now())
    if claimed is None:
        _fail("Could not claim the task")
    outcome = await process_task(claimed, deps)
    res = outcome.result.model_dump(mode="json") if outcome.result else {}
    _echo(
        f"{action.value}: {res.get('status', outcome.status.value)}"
        + (f" · {outcome.note}" if outcome.note else "")
    )
    data = {k: v for k, v in res.get("data", {}).items() if v}
    if data:
        import json

        _echo(json.dumps(data, indent=2, ensure_ascii=False))


@app.command()
def visit(url: str) -> None:
    """Visit a profile and print what was scraped."""
    _run(_with_app(lambda a: _one_off(a, Action.VISIT, url, {})))


@app.command()
def follow(url: str) -> None:
    _run(_with_app(lambda a: _one_off(a, Action.FOLLOW, url, {})))


@app.command()
def like(url: str, post_url: str = typer.Option(None)) -> None:
    """Like the newest post (or a specific post URL)."""
    _run(
        _with_app(
            lambda a: _one_off(a, Action.LIKE_POST, url, {"post_url": post_url} if post_url else {})
        )
    )


@app.command()
def comment(
    url: str,
    text: str = typer.Option(
        None, help="Comment text; omit to let the model draft it (asks first)"
    ),
    post_url: str = typer.Option(None),
) -> None:
    """Comment on the newest post (or a specific post URL)."""

    async def go(app_: App) -> None:
        params: dict[str, Any] = {"post_url": post_url} if post_url else {}
        final = text
        if not final:
            lead = await app_.deps.leads.find(url)
            camp = app_.deps.campaigns.get(lead.campaign) if lead else None
            if not (lead and camp and app_.deps.text_llm):
                _fail(
                    "Without --text the lead must be imported into a campaign and an API key set."
                )
            post = seqeng.pick_post(lead, "newest")
            if post is None:
                _fail("No known post for this lead yet; run `visit` first.")
            draft, problems = await msg.draft_comment(post, lead, camp, app_.deps.text_llm)
            if draft is None:
                _fail("Draft rejected: " + "; ".join(problems))
            _echo(f"Post: {post.text[:200]}\nDraft: {draft}")
            if not typer.confirm("Post this comment?"):
                raise typer.Exit()
            final = draft
        problems = msg.check_comment(final)
        if problems:
            _fail("Comment rejected: " + "; ".join(problems))
        await _one_off(app_, Action.COMMENT_POST, url, {**params, "text": final})

    _run(_with_app(go))


@app.command()
def connect(
    url: str, note: str = typer.Option("", help="Note text; empty sends without a note")
) -> None:
    _run(_with_app(lambda a: _one_off(a, Action.CONNECT, url, {"note": note})))


@app.command()
def message(url: str, text: str = typer.Option(..., help="Message text")) -> None:
    _run(_with_app(lambda a: _one_off(a, Action.MESSAGE, url, {"text": text})))


@app.command()
def inmail(url: str, subject: str = typer.Option(...), text: str = typer.Option(...)) -> None:
    _run(_with_app(lambda a: _one_off(a, Action.INMAIL, url, {"subject": subject, "text": text})))


@app.command()
def check(
    url: str,
    replies: bool = typer.Option(
        False, "--replies", help="Check for replies instead of connection state"
    ),
) -> None:
    """Read-only: connection state, or whether they replied."""
    action = Action.CHECK_REPLIES if replies else Action.CHECK_CONNECTION
    _run(_with_app(lambda a: _one_off(a, action, url, {})))


@app.command()
def withdraw(url: str) -> None:
    _run(_with_app(lambda a: _one_off(a, Action.WITHDRAW_INVITE, url, {})))


if __name__ == "__main__":
    app()
