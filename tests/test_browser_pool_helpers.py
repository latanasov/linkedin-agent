"""Pure helpers of the browser layer: executable resolution and the plain-Chrome launcher."""

from pathlib import Path

import pytest

from linkedin_agent.config import Settings
from linkedin_agent.core import browser_pool as bp


async def test_resolve_uses_chrome_path_when_set(tmp_path: Path):
    exe = tmp_path / "chrome"
    exe.write_text("")
    s = Settings(home=tmp_path, chrome_path=exe, _env_file=None)  # type: ignore[call-arg]
    assert await bp.resolve_chrome_executable(s) == exe


async def test_resolve_rejects_missing_chrome_path(tmp_path: Path):
    s = Settings(home=tmp_path, chrome_path=tmp_path / "nope", _env_file=None)  # type: ignore[call-arg]
    with pytest.raises(FileNotFoundError, match="CHROME_PATH"):
        await bp.resolve_chrome_executable(s)


def test_plain_chrome_command_shape(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bp, "_running_as_root", lambda: False)
    cmd = bp.plain_chrome_command(
        Path("/x/chrome"), tmp_path / "p", "https://www.linkedin.com/login"
    )
    assert cmd[0] == "/x/chrome"
    assert cmd[1] == f"--user-data-dir={tmp_path / 'p'}"
    assert "--no-first-run" in cmd and "--no-sandbox" not in cmd
    assert cmd[-1] == "https://www.linkedin.com/login"
    monkeypatch.setattr(bp, "_running_as_root", lambda: True)
    assert "--no-sandbox" in bp.plain_chrome_command(Path("/x/chrome"), tmp_path, "u")


def test_launch_plain_chrome_creates_profile_and_spawns(tmp_path: Path, monkeypatch):
    calls = []

    class FakeProc:
        def poll(self):
            return 0

    def fake_popen(args, **kw):
        calls.append(args)
        return FakeProc()

    monkeypatch.setattr(bp.subprocess, "Popen", fake_popen)
    profile = tmp_path / "profiles" / "default"
    proc = bp.launch_plain_chrome(Path("/x/chrome"), profile, "https://www.linkedin.com/login")
    assert profile.exists() and calls and calls[0][0] == "/x/chrome"
    bp.stop_plain_chrome(proc)  # already exited: no-op


def test_stop_plain_chrome_terminates_then_kills():
    events = []

    class Proc:
        def poll(self):
            return None

        def terminate(self):
            events.append("terminate")

        def wait(self, timeout=None):
            if "kill" not in events:
                raise bp.subprocess.TimeoutExpired("chrome", timeout)
            events.append("waited")

        def kill(self):
            events.append("kill")

    bp.stop_plain_chrome(Proc(), timeout=0.01)
    assert events == ["terminate", "kill", "waited"]


def test_url_looks_logged_out():
    assert bp.url_looks_logged_out("https://www.linkedin.com/login?x")
    assert bp.url_looks_logged_out("https://www.linkedin.com/checkpoint/challenge/")
    assert not bp.url_looks_logged_out("https://www.linkedin.com/feed/")


# ── ensure_tab / get_browser health ──────────────────────────────────────


class _Tab:
    def __init__(self, target_id: str) -> None:
        self.target_id = target_id
        self.url = "about:blank"


class _Bus:
    def __init__(self, session: "_Session") -> None:
        self._s = session

    def dispatch(self, event):  # SwitchTabEvent / CloseTabEvent
        async def _run():
            name = type(event).__name__
            if name == "SwitchTabEvent":
                self._s.agent_focus_target_id = event.target_id
            elif name == "CloseTabEvent":
                self._s.tabs = [t for t in self._s.tabs if t.target_id != event.target_id]
                if self._s.agent_focus_target_id == event.target_id:
                    self._s.agent_focus_target_id = None

        return _run()


class _Session:
    def __init__(self, tabs, focus, *, process_alive=True) -> None:
        self.tabs = [_Tab(t) for t in tabs]
        self.agent_focus_target_id = focus
        self.process_alive = process_alive
        self.event_bus = _Bus(self)
        self.new_pages = 0
        self.navigations: list[str] = []
        self.killed = False

    async def get_tabs(self):
        return list(self.tabs)

    async def new_page(self, url):
        if not self.process_alive:
            raise RuntimeError("Failed to open new tab - no browser is open")
        self.new_pages += 1
        tab = _Tab(f"new{self.new_pages}")
        self.tabs.append(tab)
        return tab

    async def navigate_to(self, url):
        self.navigations.append(url)

    async def kill(self):
        self.killed = True


pytest.importorskip("browser_use")


async def test_ensure_tab_is_noop_when_focused_tab_exists():
    s = _Session(["a"], "a")
    assert await bp.ensure_tab(s) is True and s.new_pages == 0


async def test_ensure_tab_refocuses_an_existing_unfocused_tab():
    s = _Session(["a"], None)
    assert await bp.ensure_tab(s) is True
    assert s.agent_focus_target_id == "a" and s.new_pages == 0


async def test_ensure_tab_opens_a_tab_when_none_remain():
    s = _Session([], None)
    assert await bp.ensure_tab(s) is True
    assert s.new_pages == 1 and s.agent_focus_target_id == "new1"


async def test_ensure_tab_reports_dead_process():
    s = _Session([], None, process_alive=False)
    assert await bp.ensure_tab(s) is False


async def test_get_browser_restarts_when_session_has_no_tab(tmp_path: Path):
    pool = bp.BrowserPool(Settings(home=tmp_path, openrouter_api_key="k"))
    created: list[_Session] = []

    async def fake_create(account, *, headless):
        s = _Session(["a"], "a")
        created.append(s)
        return s

    pool._create_browser = fake_create  # type: ignore[method-assign]
    first = await pool.get_browser("default")
    assert first is created[0]
    # healthy: same browser is reused
    assert await pool.get_browser("default") is first
    # the browser loses its last tab and the process is gone
    first.tabs, first.agent_focus_target_id, first.process_alive = [], None, False
    second = await pool.get_browser("default")
    assert second is created[1] and first.killed


async def test_cleanup_marks_browser_dead_when_no_tab_survives(tmp_path: Path):
    pool = bp.BrowserPool(Settings(home=tmp_path, openrouter_api_key="k"))
    s = _Session(["a", "b"], "b", process_alive=False)

    async def fake_create(account, *, headless):
        return s

    pool._create_browser = fake_create  # type: ignore[method-assign]
    await pool.get_browser("default")
    # simulate the agent's tab vanishing mid-cleanup
    s.tabs, s.agent_focus_target_id = [], None
    await pool.cleanup_pages()
    assert pool.has_active_browser is False


async def test_verify_session_uses_feed_redirect(tmp_path: Path, monkeypatch):
    pool = bp.BrowserPool(Settings(home=tmp_path, openrouter_api_key="k"))
    s = _Session(["a"], "a")

    async def fake_create(account, *, headless):
        return s

    async def fake_verify(browser):
        return True, "https://www.linkedin.com/feed/"

    pool._create_browser = fake_create  # type: ignore[method-assign]
    monkeypatch.setattr(bp, "verify_logged_in", fake_verify)
    assert await pool.verify_session("default") is None  # no browser yet
    await pool.get_browser("default")
    assert await pool.verify_session("default") is True
    assert await pool.verify_session("other") is None
