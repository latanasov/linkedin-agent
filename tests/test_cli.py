"""CLI tests through typer's CliRunner with the browser/LLM layer faked."""

from __future__ import annotations

import asyncio
import random
from pathlib import Path

import pytest
from typer.testing import CliRunner

from linkedin_agent import bootstrap, cli
from linkedin_agent.adapters.sqlite import (
    Database,
    SqliteAccountStore,
    SqliteActionLog,
    SqliteLeadStore,
    SqliteReviewQueue,
    SqliteTaskQueue,
)
from linkedin_agent.campaigns import load_all_user_campaigns
from linkedin_agent.core.runner import Deps
from tests.conftest import NOW, FakeExecutor, FakeLLM, FakePool

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("LINKEDIN_AGENT_HOME", str(h))
    monkeypatch.setenv("LINKEDIN_AGENT_OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("LINKEDIN_AGENT_DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("LINKEDIN_AGENT_MIN_DELAY_S", "0")
    monkeypatch.setenv("LINKEDIN_AGENT_MAX_DELAY_S", "0")
    return h


@pytest.fixture
def fakes(monkeypatch):
    executor, pool, llm = FakeExecutor(), FakePool(), FakeLLM()

    async def fake_build_app(settings, *, campaigns=None, need_llm=True):
        settings.home.mkdir(parents=True, exist_ok=True)
        db = await Database(settings.database_path).open()
        deps = Deps(
            settings=settings,
            queue=SqliteTaskQueue(db),
            leads=SqliteLeadStore(db),
            log=SqliteActionLog(db),
            accounts=SqliteAccountStore(db),
            review=SqliteReviewQueue(db),
            executor=executor,
            pool=pool,
            campaigns=campaigns if campaigns is not None else load_all_user_campaigns(settings),
            text_llm=llm,
            clock=lambda: NOW,  # Wednesday 10:00 UTC: inside every window for European leads
            rng=random.Random(7),
        )
        return bootstrap.App(settings=settings, db=db, deps=deps)

    monkeypatch.setattr(cli, "build_app", fake_build_app)
    return executor, pool, llm


def csv_file(tmp_path: Path) -> Path:
    p = tmp_path / "leads.csv"
    p.write_text(
        "linkedin_url,first_name,last_name,company,title,location\n"
        'https://www.linkedin.com/in/janedoe,Jane,Doe,Acme,VP Eng,"London, UK"\n'
        "https://www.linkedin.com/in/bobsmith,Bob,Smith,Contoso,CTO,Berlin\n"
        "not-a-url,X,Y,,,\n"
    )
    return p


def test_version():
    r = runner.invoke(cli.app, ["version"])
    assert r.exit_code == 0 and "linkedin-agent" in r.output


def test_campaign_new_list_check_show(home):
    r = runner.invoke(cli.app, ["campaign", "new", "mine"])
    assert r.exit_code == 0, r.output
    assert (home / "campaigns" / "mine.yaml").exists()
    r = runner.invoke(cli.app, ["campaign", "list"])
    assert "mine" in r.output and "default" in r.output
    r = runner.invoke(cli.app, ["campaign", "check", "mine"])
    assert r.exit_code == 0 and "OK" in r.output
    r = runner.invoke(cli.app, ["campaign", "show", "mine"])
    assert "warm.visit" in r.output
    # break it and check again
    p = home / "campaigns" / "mine.yaml"
    p.write_text(p.read_text().replace("{first_name}", "{firstname}"))
    r = runner.invoke(cli.app, ["campaign", "check", "mine"])
    assert r.exit_code == 1 and "firstname" in r.output
    r = runner.invoke(cli.app, ["campaign", "new", "mine"])
    assert r.exit_code == 1


def test_import_preview_status_run_report(home, fakes, tmp_path):
    executor, pool, llm = fakes
    runner.invoke(cli.app, ["campaign", "new", "mine"])
    r = runner.invoke(cli.app, ["import", str(csv_file(tmp_path)), "--campaign", "mine"])
    assert r.exit_code == 0, r.output
    assert "2 new" in r.output and "1 skipped" in r.output and "Sequences started: 2" in r.output

    r = runner.invoke(cli.app, ["preview", "janedoe"])
    assert r.exit_code == 0, r.output
    assert "[m1]" in r.output and "Hi Jane, thanks for connecting." in r.output
    assert "[connection_note]" in r.output

    r = runner.invoke(cli.app, ["status"])
    assert r.exit_code == 0 and "NOT logged in" in r.output and "leads: new 2" in r.output

    executor.script  # noqa: B018 - default results are fine
    r = runner.invoke(cli.app, ["run", "--once"])
    assert r.exit_code == 0, r.output
    assert "tick" in r.output and "visit" in r.output and "Processed 2 task(s)" in r.output

    r = runner.invoke(cli.app, ["status"])
    assert "warming 2" in r.output and "visit 2/" in r.output

    r = runner.invoke(cli.app, ["report", "--since", "7d"])
    assert r.exit_code == 0 and "invites sent        0" in r.output
    out = tmp_path / "rep.csv"
    r = runner.invoke(cli.app, ["report", "--csv", str(out)])
    assert out.exists() and "janedoe" in out.read_text()

    r = runner.invoke(cli.app, ["log"])
    assert "visit" in r.output
    r = runner.invoke(cli.app, ["log", "--comments"])
    assert "No comments" in r.output

    r = runner.invoke(cli.app, ["inbox"])
    assert "Inbox empty" in r.output
    r = runner.invoke(cli.app, ["review", "--list"])
    assert "Nothing to review" in r.output


def test_pause_resume_retry_skip_breaker(home, fakes, tmp_path):
    runner.invoke(cli.app, ["campaign", "new", "mine"])
    runner.invoke(cli.app, ["import", str(csv_file(tmp_path)), "--campaign", "mine"])
    r = runner.invoke(cli.app, ["pause", "mine"])
    assert "Paused 2" in r.output
    r = runner.invoke(cli.app, ["run", "--once"])
    assert "Processed 0" in r.output
    r = runner.invoke(cli.app, ["resume", "mine"])
    assert "Resumed 2" in r.output
    r = runner.invoke(cli.app, ["skip", "janedoe"])
    assert "skipped warm.visit" in r.output
    r = runner.invoke(cli.app, ["retry", "janedoe"])
    assert "re-armed" in r.output
    r = runner.invoke(cli.app, ["retry", "nobody"])
    assert r.exit_code == 1
    r = runner.invoke(cli.app, ["restart", "janedoe", "--step", "wait.accept"])
    assert "restarted at wait.accept (stage invited" in r.output
    r = runner.invoke(cli.app, ["restart", "janedoe"])
    assert "restarted at warm.visit (stage new" in r.output
    r = runner.invoke(cli.app, ["restart", "janedoe", "--step", "nope"])
    assert "unknown step" in r.output
    r = runner.invoke(cli.app, ["restart", "nobody"])
    assert r.exit_code == 1
    r = runner.invoke(cli.app, ["breaker", "status"])
    assert "ok" in r.output
    r = runner.invoke(cli.app, ["breaker", "reset"])
    assert "reset" in r.output


def test_one_off_commands(home, fakes):
    executor, _, _ = fakes
    runner.invoke(cli.app, ["campaign", "new", "mine"])
    from linkedin_agent.models import Action

    executor.script(Action.CHECK_CONNECTION, {"status": "connected"})
    r = runner.invoke(cli.app, ["check", "https://www.linkedin.com/in/someone/"])
    assert r.exit_code == 0 and "connected" in r.output
    executor.script(Action.VISIT, {"status": "ok", "headline": "CTO"})
    r = runner.invoke(cli.app, ["visit", "https://www.linkedin.com/in/someone/"])
    assert '"headline": "CTO"' in r.output
    r = runner.invoke(
        cli.app, ["connect", "https://www.linkedin.com/in/someone/", "--note", "Hello"]
    )
    assert "connect: sent" in r.output
    r = runner.invoke(
        cli.app, ["message", "https://www.linkedin.com/in/someone/", "--text", "Hi there"]
    )
    assert "message: sent" in r.output
    r = runner.invoke(
        cli.app, ["comment", "https://www.linkedin.com/in/someone/", "--text", "Great post!"]
    )
    assert r.exit_code == 1 and "banned" in r.output
    r = runner.invoke(cli.app, ["visit", "https://evil.com/in/x"])
    assert r.exit_code == 1


def test_run_without_campaigns_executes_queued_one_offs(home, fakes, monkeypatch):
    executor = fakes[0]
    r = runner.invoke(cli.app, ["run", "--once"])
    assert r.exit_code == 0, r.output
    assert "No campaign files loaded" in r.output and "Processed 0" in r.output
    # queue a one-off (what the MCP server does) and the loop runs it, no campaign involved
    from linkedin_agent.service import Service

    async def queue_visit():
        app_ = await cli.build_app(cli._settings(), need_llm=False)
        try:
            return await Service(app_.deps, app_.settings).enqueue_action(
                "visit", "https://www.linkedin.com/in/janedoe"
            )
        finally:
            await app_.close()

    out = asyncio.run(queue_visit())
    r = runner.invoke(cli.app, ["run", "--once"])
    assert "visit" in r.output and "Processed 1" in r.output
    assert executor.calls[-1].profile_url == "https://www.linkedin.com/in/janedoe" and out["queued"]
    monkeypatch.setenv("LINKEDIN_AGENT_OPENROUTER_API_KEY", "")
    r = runner.invoke(cli.app, ["run", "--once"])
    assert r.exit_code == 1 and "OPENROUTER" in r.output


def test_init_writes_env(home):
    r = runner.invoke(cli.app, ["init"], input="Europe/Sofia\npro\n\nsk-test\n")
    assert r.exit_code == 0, r.output
    env = (home / ".env").read_text()
    assert "LINKEDIN_AGENT_OPENROUTER_API_KEY=sk-test" in env and "Europe/Sofia" in env


def test_verbose_keeps_sqlite_and_asyncio_quiet(home, fakes):
    import logging

    runner.invoke(cli.app, ["-v", "version"])
    assert logging.getLogger("aiosqlite").level == logging.WARNING
    assert logging.getLogger("asyncio").level == logging.WARNING
    assert logging.getLogger("browser_use").level != logging.CRITICAL
    runner.invoke(cli.app, ["version"])
    assert logging.getLogger("browser_use").level == logging.CRITICAL


# ── assistant-driven setup: two-step login, key-less init, doctor ───────


def test_init_can_skip_the_key_and_take_flags(home):
    r = runner.invoke(
        cli.app,
        ["init", "--skip-key", "--timezone", "Europe/Sofia", "--tier", "pro", "--chrome-path", ""],
    )
    assert r.exit_code == 0, r.output
    env = (home / ".env").read_text()
    assert "LINKEDIN_AGENT_OPENROUTER_API_KEY=\n" in env and "Europe/Sofia" in env
    assert "Paste your OpenRouter key" in r.output


def test_login_open_then_verify(home, fakes, monkeypatch, tmp_path):
    launched: list[tuple] = []

    class Proc:
        def poll(self):
            return None

    monkeypatch.setattr(cli, "resolve_chrome_executable", _async(lambda s: tmp_path / "chrome"))
    monkeypatch.setattr(
        cli,
        "launch_plain_chrome",
        lambda exe, prof, url: launched.append((exe, prof, url)) or Proc(),
    )
    r = runner.invoke(cli.app, ["login", "--open"])
    assert r.exit_code == 0, r.output
    assert launched and launched[0][2].endswith("/login") and "login --verify" in r.output

    monkeypatch.setattr(
        cli, "verify_logged_in", _async(lambda b: (True, "https://www.linkedin.com/feed/"))
    )
    monkeypatch.setattr(cli, "user_agent_of", _async(lambda b: "UA"))
    r = runner.invoke(cli.app, ["login", "--verify"])
    assert r.exit_code == 0, r.output
    assert "Logged in" in r.output and len(launched) == 1  # no second window

    monkeypatch.setattr(
        cli, "verify_logged_in", _async(lambda b: (False, "https://www.linkedin.com/login"))
    )
    r = runner.invoke(cli.app, ["login", "--verify"])
    assert r.exit_code == 1 and "Still not logged in" in r.output


def test_doctor_reports_each_check(home, fakes, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "resolve_chrome_executable", _async(lambda s: tmp_path / "chrome"))
    r = runner.invoke(cli.app, ["doctor"])
    assert r.exit_code == 1
    assert "OpenRouter API key set" in r.output and "FAIL LinkedIn login" in r.output
    assert "note campaigns: none" in r.output and "note leads: 0" in r.output
    assert "run loop not active" in r.output and "need attention" in r.output

    runner.invoke(cli.app, ["campaign", "new", "mine"])
    runner.invoke(cli.app, ["import", str(csv_file(tmp_path)), "--campaign", "mine"])
    monkeypatch.setattr(
        cli, "verify_logged_in", _async(lambda b: (True, "https://www.linkedin.com/feed/"))
    )
    monkeypatch.setattr(cli, "user_agent_of", _async(lambda b: "UA"))
    runner.invoke(cli.app, ["login", "--verify"])
    (home / ".env").write_text("LINKEDIN_AGENT_OPENROUTER_API_KEY=k\n")
    r = runner.invoke(cli.app, ["doctor"])
    assert "ok   LinkedIn login verified" in r.output and "campaign mine valid" in r.output
    assert "leads: 2" in r.output


def _async(fn):
    async def inner(*a, **k):
        return fn(*a, **k)

    return inner


def test_doctor_checks_ollama_when_it_is_the_provider(home, fakes, monkeypatch, tmp_path):
    monkeypatch.setenv("LINKEDIN_AGENT_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LINKEDIN_AGENT_BROWSER_LLM_MODEL", "qwen3.5:35b")
    monkeypatch.setenv("LINKEDIN_AGENT_TEXT_LLM_MODEL", "gemma4:12b")
    monkeypatch.setattr(cli, "resolve_chrome_executable", _async(lambda s: tmp_path / "chrome"))

    async def fake_models(host, timeout=5.0):
        return ["qwen3.5:35b"]

    monkeypatch.setattr("linkedin_agent.llm.ollama_models", fake_models)
    r = runner.invoke(cli.app, ["doctor"])
    assert "ok   Ollama at http://localhost:11434" in r.output
    assert "ok   Ollama browser model qwen3.5:35b" in r.output
    assert "FAIL Ollama text model gemma4:12b" in r.output and "ollama pull gemma4:12b" in r.output
    assert "OpenRouter API key" not in r.output
    # the model loop must not shadow the account name: login and profile still use "default"
    assert "profiles/default" in r.output
    assert "profiles/gemma4" not in r.output and "profiles/qwen3.5" not in r.output


def test_doctor_checks_each_role_where_it_runs(home, fakes, monkeypatch, tmp_path):
    """Browser on OpenRouter, text on Ollama: the key is required and only the text model
    has to be pulled."""
    monkeypatch.setenv("LINKEDIN_AGENT_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LINKEDIN_AGENT_BROWSER_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LINKEDIN_AGENT_BROWSER_LLM_MODEL", "google/gemini-2.5-flash")
    monkeypatch.setenv("LINKEDIN_AGENT_TEXT_LLM_MODEL", "gemma4:12b")
    monkeypatch.setattr(cli, "resolve_chrome_executable", _async(lambda s: tmp_path / "chrome"))

    async def fake_models(host, timeout=5.0):
        return ["gemma4:12b"]

    monkeypatch.setattr("linkedin_agent.llm.ollama_models", fake_models)
    r = runner.invoke(cli.app, ["doctor"])
    assert "browser model google/gemini-2.5-flash via openrouter" in r.output
    assert "text model gemma4:12b via ollama" in r.output
    assert "OpenRouter API key set" in r.output
    assert "ok   Ollama text model gemma4:12b" in r.output
    assert "Ollama browser model" not in r.output
