import pytest

from linkedin_agent.core.errors import classify_error, classify_result
from linkedin_agent.models import ErrorKind, TaskResult


@pytest.mark.parametrize(
    "text,kind",
    [
        ("Target closed", ErrorKind.CRASH),
        ("Browser has been closed", ErrorKind.CRASH),
        ("CDP client not initialized", ErrorKind.CRASH),
        ("Redirected to https://www.linkedin.com/login", ErrorKind.SESSION_EXPIRED),
        ("checkpoint challenge", ErrorKind.SESSION_EXPIRED),
        ("HTTP 429 Too Many Requests", ErrorKind.RESTRICTED),
        ("Your account has been restricted", ErrorKind.RESTRICTED),
        ("We noticed unusual activity", ErrorKind.RESTRICTED),
        ("Element not found", ErrorKind.OTHER),
        ("", ErrorKind.OTHER),
    ],
)
def test_classify_error_strings(text, kind):
    assert classify_error(text) == kind
    assert classify_error(RuntimeError(text)) == kind


def test_classify_error_uses_class_name_when_message_empty():
    class BrowserCrashed(Exception):
        pass

    assert classify_error(BrowserCrashed()) == ErrorKind.CRASH


def test_classify_result():
    assert (
        classify_result(TaskResult(status="failed", error="login_required"))
        == ErrorKind.SESSION_EXPIRED
    )
    assert classify_result(TaskResult(status="failed", error="restricted")) == ErrorKind.RESTRICTED
    assert classify_result(TaskResult(status="failed", error="element missing")) == ErrorKind.OTHER
    assert classify_result(TaskResult(status="error")) == ErrorKind.OTHER
    assert classify_result(TaskResult(status="sent")) is None
    assert classify_result(TaskResult(status="cannot_message")) is None


def test_classify_result_dead_browser_is_a_crash():
    assert (
        classify_result(TaskResult(status="failed", error="page_did_not_load")) == ErrorKind.CRASH
    )
    assert classify_result(TaskResult(status="failed", error="The DOM is empty")) == ErrorKind.CRASH
    # a real LinkedIn status is untouched
    assert classify_result(TaskResult(status="not_connected")) is None


@pytest.mark.parametrize(
    "text",
    [
        "Event handler BrowserSession.on_SwitchTabEvent#3c41(SwitchTabEvent) timed out after 10s",
        "LLM call timed out after 60 seconds. Keep your thinking and output short.",
        "TIMEOUT ERROR - Handling took more than 10.0s for EventBus.on_SwitchTabEvent",
    ],
)
def test_timeouts_are_crashes_not_linkedin_failures(text):
    """A slow browser or a slow model is never a LinkedIn signal: retried without using
    the task's attempts and without counting toward the breaker."""
    assert classify_error(text) is ErrorKind.CRASH
    assert classify_result(TaskResult(status="failed", error=text)) is ErrorKind.CRASH


def test_bare_timeout_error_is_classified_by_its_class_name():
    import asyncio

    assert classify_error(asyncio.TimeoutError()) is ErrorKind.CRASH
    assert classify_error(TimeoutError()) is ErrorKind.CRASH
