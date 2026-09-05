"""Pin guard: every browser-use attribute the pool and executor rely on must exist."""

import importlib.metadata
import inspect

import pytest

browser_use = pytest.importorskip("browser_use")


def test_pinned_version():
    assert importlib.metadata.version("browser-use") == "0.11.3"


def test_browser_session_surface():
    from browser_use.browser.session import BrowserSession

    for name in (
        "start",
        "kill",
        "get_tabs",
        "close_page",
        "navigate_to",
        "get_current_page_url",
        "get_or_create_cdp_session",
        "new_page",
    ):
        assert callable(getattr(BrowserSession, name)), name
    # ensure_tab reads this to detect the "no focused tab" state that no-ops navigation
    assert "agent_focus_target_id" in BrowserSession.model_fields or hasattr(
        BrowserSession, "agent_focus_target_id"
    )
    params = inspect.signature(BrowserSession.__init__).parameters
    for kw in ("user_data_dir", "headless", "keep_alive", "executable_path", "args", "proxy"):
        assert kw in params, kw


def test_agent_and_llm_surface():
    from browser_use import Agent, Browser
    from browser_use.llm.openrouter.chat import ChatOpenRouter

    assert Browser is not None
    params = inspect.signature(Agent.__init__).parameters
    for kw in (
        "task",
        "llm",
        "browser",
        "max_actions_per_step",
        "max_failures",
        "use_judge",
        # We raise both for local models; browser-use's own defaults (60s / 120s) are
        # shorter than one step of a 40k-token prompt on a laptop.
        "llm_timeout",
        "step_timeout",
    ):
        assert kw in params, kw
    assert "max_steps" in inspect.signature(Agent.run).parameters
    assert "api_key" in inspect.signature(ChatOpenRouter).parameters
    from browser_use.agent.views import AgentHistoryList

    assert callable(AgentHistoryList.final_result)


def test_signal_handler_surface_and_neutralisation():
    """We disable browser-use's process-wide signal handling; the class and both methods
    it installs through must exist, and after neutralisation registering does nothing."""
    import asyncio
    import signal

    from browser_use.utils import SignalHandler

    from linkedin_agent.core.prompts import keep_our_signal_handlers

    assert callable(SignalHandler.register) and callable(SignalHandler.unregister)
    keep_our_signal_handlers()
    loop = asyncio.new_event_loop()
    try:
        before = signal.getsignal(signal.SIGTERM)
        h = SignalHandler(loop=loop)
        h.register()
        assert signal.getsignal(signal.SIGTERM) is before, "register must not touch SIGTERM"
        h.unregister()
        assert signal.getsignal(signal.SIGTERM) is before
    finally:
        loop.close()
