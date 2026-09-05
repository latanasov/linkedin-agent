"""Classify exceptions and results from browser tasks. Pure."""

from __future__ import annotations

from ..models import ErrorKind, TaskResult

CRASH_INDICATORS = (
    "target closed",
    "browser has been closed",
    "connection refused",
    "browser disconnected",
    "crashed",
    "protocol error",
    "cdp client",
    "not connected",
    "websocket",
    # Timeouts are the browser or the model being slow, never a LinkedIn signal:
    # bubus "Event handler … timed out after 10s", browser-use "LLM call timed out",
    # a bare asyncio.TimeoutError (classified by its class name).
    "timed out",
    "timeouterror",
    "timeout error",
    "took more than",
)

SESSION_EXPIRED_INDICATORS = (
    "login",
    "checkpoint",
    "sign in",
    "session expired",
    "unauthorized",
    "authwall",
)

RESTRICTION_INDICATORS = (
    "restricted",
    "unusual activity",
    "temporarily limited",
    "429",
    "too many requests",
    "verify your identity",
)

# Result statuses / error codes that mean the same thing when the task itself
# reports them instead of raising.
RESULT_SESSION_EXPIRED = ("login_required", "session_expired", "authwall", "checkpoint")
RESULT_RESTRICTED = ("restricted", "rate_limited_by_linkedin", "unusual_activity")
# What the model reports when the browser underneath it is gone (empty DOM, no navigation).
RESULT_CRASH = (
    "page_did_not_load",
    "page did not load",
    "browser not connected",
    "dom is empty",
    "empty page",
    "no browser",
    "blank page",
    "timed out",
    "timeout error",
)


def classify_error(exc: BaseException | str) -> ErrorKind:
    if isinstance(exc, BaseException):
        text = str(exc) or exc.__class__.__name__
    else:
        text = str(exc)
    text = text.lower()
    if any(k in text for k in CRASH_INDICATORS):
        return ErrorKind.CRASH
    if any(k in text for k in SESSION_EXPIRED_INDICATORS):
        return ErrorKind.SESSION_EXPIRED
    if any(k in text for k in RESTRICTION_INDICATORS):
        return ErrorKind.RESTRICTED
    return ErrorKind.OTHER


def classify_result(result: TaskResult) -> ErrorKind | None:
    """A task may report a problem in its JSON rather than raise. Map that too."""
    probe = " ".join(filter(None, (result.status, result.error))).lower()
    if not probe:
        return None
    if any(k in probe for k in RESULT_CRASH):
        return ErrorKind.CRASH
    if any(k in probe for k in RESULT_SESSION_EXPIRED):
        return ErrorKind.SESSION_EXPIRED
    if any(k in probe for k in RESULT_RESTRICTED):
        return ErrorKind.RESTRICTED
    if result.status in ("failed", "error"):
        return ErrorKind.OTHER
    return None
