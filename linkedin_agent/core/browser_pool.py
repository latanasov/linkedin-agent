"""One reusable Chromium per account, backed by a persistent Chrome profile directory.

Built for browser-use 0.11:
- cookies live in user_data_dir, so no storage_state juggling or cookie renewal
- tab cleanup uses get_tabs()/close_page() (browser_context no longer exists)
- restarts after N tasks, on memory pressure, on crash, and on account switch
"""

from __future__ import annotations

import asyncio
import gc
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from ..config import Settings

logger = logging.getLogger(__name__)

MEMORY_THRESHOLD_PCT = 70

CHROMIUM_FLAGS = [
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-sync",
    "--disable-translate",
    "--metrics-recording-only",
    "--no-first-run",
    "--js-flags=--max-old-space-size=256",
]
HEADLESS_ONLY_FLAGS = ["--disable-gpu"]


async def resolve_chrome_executable(settings: Settings) -> Path:
    """The one Chromium binary used for login AND tasks.

    Chrome encrypts profile cookies with a per-binary OS keychain entry, so the browser
    that logs in must be the same binary that later runs tasks on that profile.
    """
    if settings.chrome_path:
        exe = Path(settings.chrome_path).expanduser()
        if not exe.exists():
            raise FileNotFoundError(f"LINKEDIN_AGENT_CHROME_PATH does not exist: {exe}")
        return exe
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:  # pragma: no cover - playwright is a hard dependency
        raise RuntimeError("playwright is not installed") from e
    async with async_playwright() as pw:
        exe = Path(pw.chromium.executable_path)
    if not exe.exists():
        raise FileNotFoundError(
            "Playwright Chromium is not installed. Run `playwright install chromium` "
            "or set LINKEDIN_AGENT_CHROME_PATH."
        )
    return exe


PLAIN_CHROME_FLAGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
    "--new-window",
]


def plain_chrome_command(exe: Path, profile_dir: Path, url: str) -> list[str]:
    args = [str(exe), f"--user-data-dir={profile_dir}", *PLAIN_CHROME_FLAGS]
    if _running_as_root():
        args.append("--no-sandbox")
    args.append(url)
    return args


def launch_plain_chrome(exe: Path, profile_dir: Path, url: str) -> subprocess.Popen[bytes]:
    """Open a normal, un-automated Chrome window on the agent's profile.

    Used for the interactive login: no CDP is attached, so sign-in popups (Google SSO,
    2FA) behave exactly as in a regular browser and nothing pauses the page.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        plain_chrome_command(exe, profile_dir, url),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_plain_chrome(proc: subprocess.Popen[bytes], timeout: float = 15) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _running_as_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


class BrowserPool:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._browser: Any = None
        self._account: str | None = None
        self._task_count = 0
        self._is_dead = False
        self._last_used = time.monotonic()
        self._max_tasks = max(1, settings.browser_max_tasks)

    # ── lifecycle ────────────────────────────────────────────────────────

    async def get_browser(self, account: str) -> Any:
        memory_pressure = self._browser is not None and self._memory_pct() >= MEMORY_THRESHOLD_PCT
        needs_new = (
            self._browser is None
            or self._is_dead
            or self._account != account
            or self._task_count >= self._max_tasks
            or memory_pressure
        )
        if needs_new:
            reason = (
                "first task"
                if self._browser is None
                else "crashed"
                if self._is_dead
                else "account switch"
                if self._account != account
                else f"memory pressure ({self._memory_pct():.0f}%)"
                if memory_pressure
                else f"task limit ({self._task_count}/{self._max_tasks})"
            )
            logger.info("Starting browser for %s (%s)", account, reason)
            await self._close_browser()
            self._browser = await self._create_browser(account, headless=self._settings.headless)
            self._account = account
            self._task_count = 0
            self._is_dead = False
        elif not await ensure_tab(self._browser):
            # The session is up but has no usable tab (the last one was closed or the
            # process died): browser-use would then silently no-op every navigation.
            logger.warning("Browser has no usable tab; restarting it")
            await self._close_browser()
            self._browser = await self._create_browser(account, headless=self._settings.headless)
            self._task_count = 0
            self._is_dead = False
        self._last_used = time.monotonic()
        return self._browser

    async def open(self, account: str, *, headless: bool) -> Any:
        """A fresh browser with an explicit headless setting (login verification)."""
        await self._close_browser()
        self._browser = await self._create_browser(account, headless=headless)
        self._account = account
        self._task_count = 0
        self._is_dead = False
        self._last_used = time.monotonic()
        return self._browser

    async def _create_browser(self, account: str, *, headless: bool) -> Any:
        from browser_use import Browser

        profile_dir: Path = self._settings.profile_dir(account)
        profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            profile_dir.chmod(0o700)
        except OSError:
            pass
        args = list(CHROMIUM_FLAGS) + (HEADLESS_ONLY_FLAGS if headless else [])
        if _running_as_root():
            args.append("--no-sandbox")  # Chromium refuses to start as root otherwise (containers)
        kwargs: dict[str, Any] = {
            "user_data_dir": str(profile_dir),
            "headless": headless,
            "keep_alive": True,
            "args": args,
            # browser-use would otherwise download uBlock & co. into the profile at startup.
            "enable_default_extensions": False,
            # LinkedIn pages keep loading well past DOMContentLoaded and swap frames while
            # doing so; give them time before snapshots and between actions.
            "minimum_wait_page_load_time": 1.5,
            "wait_for_network_idle_page_load_time": 2.5,
            "wait_between_actions": 1.0,
        }
        kwargs["executable_path"] = str(await resolve_chrome_executable(self._settings))
        if self._settings.proxy_url:
            from urllib.parse import urlparse

            parsed = urlparse(self._settings.proxy_url)
            server = f"{parsed.scheme}://{parsed.hostname}" + (
                f":{parsed.port}" if parsed.port else ""
            )
            proxy: dict[str, str] = {"server": server}
            if parsed.username or parsed.password:
                proxy["username"] = parsed.username or ""
                proxy["password"] = parsed.password or ""
            kwargs["proxy"] = proxy
        browser = Browser(**kwargs)
        await browser.start()
        return browser

    async def _close_browser(self) -> None:
        if self._browser is not None:
            try:
                await asyncio.wait_for(self._browser.kill(), timeout=30)
            except asyncio.TimeoutError:
                logger.warning("Browser kill timed out after 30s")
            except Exception:
                logger.debug("Browser close failed", exc_info=True)
            self._browser = None

    async def shutdown(self) -> None:
        await self._close_browser()
        self._account = None
        self._task_count = 0
        self._is_dead = False

    async def maybe_close_idle(self, idle_seconds: float) -> None:
        if self._browser is not None and time.monotonic() - self._last_used > idle_seconds:
            logger.info("Closing idle browser")
            await self._close_browser()

    # ── per-task hooks ───────────────────────────────────────────────────

    def increment_task_count(self) -> None:
        self._task_count += 1
        self._last_used = time.monotonic()

    def mark_browser_dead(self) -> None:
        self._is_dead = True

    async def cleanup_pages(self) -> None:
        """Close the tabs a task opened so they do not accumulate across tasks.

        Uses browser-use's event bus (CloseTabEvent / SwitchTabEvent) so the session's
        own bookkeeping of targets stays consistent; raw CDP closeTarget leaves it stale.
        browser-use keeps a blank tab alive when others close, so after cleanup the
        browser holds one or two tabs, never more; verified against 0.11.3.
        """
        if self._browser is None or self._is_dead:
            return
        try:
            from browser_use.browser.events import CloseTabEvent, SwitchTabEvent

            tabs = await self._browser.get_tabs()
            if not tabs:
                if not await ensure_tab(self._browser):
                    logger.warning("Browser has no tabs and cannot open one; marked for restart")
                    self._is_dead = True
                return
            keep = tabs[0].target_id
            # Focus the survivor first: closing the focused tab makes browser-use open a
            # replacement blank tab, which would defeat the cleanup.
            try:
                event = self._browser.event_bus.dispatch(SwitchTabEvent(target_id=keep))
                await event
            except Exception:
                logger.debug("Switching to first tab failed", exc_info=True)
            for tab in tabs[1:]:
                try:
                    event = self._browser.event_bus.dispatch(CloseTabEvent(target_id=tab.target_id))
                    await event
                except Exception:
                    logger.debug("Closing tab %s failed", tab.target_id, exc_info=True)
            if not await ensure_tab(self._browser):
                logger.warning("No tab survived cleanup; browser marked for restart")
                self._is_dead = True
                return
            try:
                await self._browser.navigate_to("about:blank")
            except Exception:
                logger.debug("Parking tab on about:blank failed", exc_info=True)
        except Exception:
            logger.debug("Tab cleanup failed", exc_info=True)

    async def verify_session(self, account: str) -> bool | None:
        """Is the LinkedIn session on the current browser really logged in?

        True/False when the feed page could be loaded and judged; None when the browser
        is not available or could not answer (caller should not draw conclusions)."""
        if self._browser is None or self._is_dead or self._account != account:
            return None
        try:
            if not await ensure_tab(self._browser):
                self._is_dead = True
                return None
            ok, _url = await verify_logged_in(self._browser)
            return ok
        except Exception:
            logger.debug("Session verification failed", exc_info=True)
            return None

    @staticmethod
    def force_gc() -> None:
        gc.collect()

    @property
    def has_active_browser(self) -> bool:
        return self._browser is not None and not self._is_dead

    @staticmethod
    def _memory_pct() -> float:
        try:
            import psutil

            return float(psutil.Process().memory_percent())
        except Exception:
            return 0.0


async def ensure_tab(browser: Any) -> bool:
    """Make sure the session has a focused page target; open one if not.

    browser-use 0.11 turns every navigation into a silent no-op once
    `agent_focus_target_id` is None, so a session with no tab looks alive while every
    task sees an empty page. Returns False when no tab can be created (process gone)."""
    try:
        tabs = await browser.get_tabs()
        if tabs and getattr(browser, "agent_focus_target_id", None):
            return True
        from browser_use.browser.events import SwitchTabEvent

        target_id = tabs[0].target_id if tabs else (await browser.new_page("about:blank")).target_id
        await browser.event_bus.dispatch(SwitchTabEvent(target_id=target_id))
        return bool(getattr(browser, "agent_focus_target_id", None))
    except Exception:
        logger.debug("ensure_tab failed", exc_info=True)
        return False


# ── login helpers (used by the CLI) ──────────────────────────────────────

LOGIN_URL = "https://www.linkedin.com/login"
FEED_URL = "https://www.linkedin.com/feed/"
LOGGED_OUT_MARKERS = ("/login", "/authwall", "/checkpoint", "/uas/", "signup")


def url_looks_logged_out(url: str) -> bool:
    low = (url or "").lower()
    return any(m in low for m in LOGGED_OUT_MARKERS)


async def current_url(browser: Any) -> str:
    try:
        return str(await browser.get_current_page_url())
    except Exception:
        tabs = await browser.get_tabs()
        return str(tabs[-1].url) if tabs else ""


async def verify_logged_in(browser: Any) -> tuple[bool, str]:
    await browser.navigate_to(FEED_URL)
    await asyncio.sleep(3)
    url = await current_url(browser)
    return (not url_looks_logged_out(url)) and "linkedin.com" in url, url


async def user_agent_of(browser: Any) -> str | None:
    try:
        session = await browser.get_or_create_cdp_session()
        result = await session.cdp_client.send.Runtime.evaluate(
            params={"expression": "navigator.userAgent", "returnByValue": True},
            session_id=session.session_id,
        )
        return str(result["result"]["value"])
    except Exception:
        return None


async def seed_li_at_cookie(browser: Any, li_at: str) -> None:
    """Fallback login path: put the li_at cookie into the persistent profile via CDP."""
    session = await browser.get_or_create_cdp_session()
    await session.cdp_client.send.Network.setCookie(
        params={
            "name": "li_at",
            "value": li_at,
            "domain": ".linkedin.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "sameSite": "None",
            "expires": time.time() + 365 * 24 * 3600,
        },
        session_id=session.session_id,
    )
