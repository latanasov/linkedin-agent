"""Shared helpers for browser tasks: URL validation, text sanitisation, browser-use runner.

browser_use is imported lazily inside run_linkedin_agent so everything else in the
package is importable without it.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

# Standard profiles and Sales Navigator leads. Imported by the API service too, so keep
# it dependency-free.
LINKEDIN_URL_RE = re.compile(
    r"^https://(?:www\.)?linkedin\.com/"
    r"(?:in/[a-zA-Z0-9À-ɏ._%-]+|sales/(?:lead|people)/[a-zA-Z0-9_,-]+)"
    r"(?:/[a-zA-Z0-9_,-]*)?"
    r"/?(?:\?.*)?$"
)

# Post / activity URLs the like and comment tasks navigate to.
LINKEDIN_POST_URL_RE = re.compile(
    r"^https://(?:www\.)?linkedin\.com/"
    r"(?:posts/[^\s]+|feed/update/urn:li:(?:activity|share|ugcPost):\d+[^\s]*)$"
)

LINKEDIN_COMPANY_URL_RE = re.compile(
    r"^https://(?:www\.)?linkedin\.com/(?:company|school|showcase)/[a-zA-Z0-9À-ɏ._%-]+/?(?:\?.*)?$"
)

JSON_ONLY_RULE = (
    "When you are done, call done with ONLY a JSON object as the text, no prose, "
    "no markdown fences."
)


def validate_linkedin_url(url: str) -> str:
    if not LINKEDIN_URL_RE.match(url or ""):
        raise ValueError(
            "Invalid LinkedIn URL: must match https://linkedin.com/in/... — "
            f"got '{(url or '')[:100]}'"
        )
    return url


def validate_post_url(url: str) -> str:
    if not LINKEDIN_POST_URL_RE.match(url or ""):
        raise ValueError(f"Invalid LinkedIn post URL: got '{(url or '')[:100]}'")
    return url


_INVISIBLE_RE = re.compile(
    "[\u200b\u200c\u200d\u200e\u200f\u00ad\ufeff\u2060\u2061\u2062\u2063\u2064]"
)
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"ignore\s+the\s+above",
    r"disregard\s+(all\s+)?prior",
    r"system\s*:",
    r"you\s+are\s+now",
    r"new\s+instructions?\s*:",
]


def sanitize_user_text(text: str, max_length: int = 8000) -> str:
    """Strip prompt-injection patterns and invisible characters from text that will be
    embedded in a browser-use prompt (notes, messages, comments, scraped post text)."""
    if not text:
        return text
    text = text[:max_length]
    text = _INVISIBLE_RE.sub("", text)
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"-{3,}", "--", text)
    text = re.sub(r"={3,}", "==", text)
    text = re.sub(r"`{3,}", "``", text)
    for pat in _INJECTION_PATTERNS:
        text = re.sub(pat, "[FILTERED]", text, flags=re.IGNORECASE)
    return text


def parse_agent_result(raw: Any) -> Any:
    """browser-use returns an AgentHistoryList; pull the final text and parse JSON if possible."""
    parsed: Any = raw
    if hasattr(raw, "final_result"):
        parsed = raw.final_result()
    elif hasattr(raw, "model_dump"):
        parsed = raw.model_dump()
    if isinstance(parsed, str):
        text = parsed.strip()
        # Tolerate ```json fences and leading prose before the object.
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.MULTILINE)
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                pass
        return parsed
    return parsed


_SIGNALS_NEUTRALIZED = False


def keep_our_signal_handlers() -> None:
    """Stop browser-use from taking over SIGINT/SIGTERM for the whole process.

    Agent.run() registers its own handlers on the event loop for the duration of a task
    (pause on Ctrl-C, os._exit(0) on SIGTERM) and on the way out removes whatever handler
    is installed — ours — and restores an "original" it recorded as None. So after the
    first browser task a SIGTERM killed the run outright, skipping the cleanup that
    closes Chrome, releases the task and clears the heartbeat; during a task it exited
    the process from inside the handler. The run loop owns the process lifecycle, so
    browser-use's register/unregister become no-ops. tests/test_browser_use_surface.py
    guards that the class and both methods still exist."""
    global _SIGNALS_NEUTRALIZED
    if _SIGNALS_NEUTRALIZED:
        return
    try:
        from browser_use.utils import SignalHandler
    except ImportError:  # pragma: no cover - browser_use is a hard dependency
        return
    SignalHandler.register = lambda self: None  # type: ignore[method-assign]
    SignalHandler.unregister = lambda self: None  # type: ignore[method-assign]
    _SIGNALS_NEUTRALIZED = True


async def run_linkedin_agent(
    prompt: str,
    browser: Any,
    llm: Any,
    *,
    max_steps: int = 12,
    max_failures: int = 2,
    llm_timeout_s: int | None = None,
    step_timeout_s: int | None = None,
) -> Any:
    """Create and run a browser-use Agent against an existing browser session.

    browser-use caps every model call at 60s and every step at 120s, which is fine for a
    hosted model and far too short for a local one reading a 40k-token page. Passing the
    timeouts through lets the Ollama path raise both; left as None, browser-use keeps its
    own per-model defaults."""
    from browser_use import Agent

    keep_our_signal_handlers()
    extra: dict[str, Any] = {}
    if llm_timeout_s is not None:
        extra["llm_timeout"] = llm_timeout_s
    if step_timeout_s is not None:
        extra["step_timeout"] = step_timeout_s

    agent: Any = Agent(
        task=prompt,
        llm=llm,
        browser=browser,
        max_actions_per_step=5,
        max_failures=max_failures,
        use_judge=False,
        **extra,
    )
    try:
        history = await agent.run(max_steps=max_steps)
        return parse_agent_result(history)
    finally:
        del agent
