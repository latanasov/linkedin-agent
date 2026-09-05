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
