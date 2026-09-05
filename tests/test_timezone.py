from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from linkedin_agent.core.timezone import (
    guess_timezone,
    in_window,
    next_window,
    resolve_tz,
    schedule_in_window,
)

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


def dt(*args, tz=UTC):
    return datetime(*args, tzinfo=tz)


@pytest.mark.parametrize(
    "loc,tz",
    [
        ("New York, NY", "America/New_York"),
        ("Greater Boston Area", "America/New_York"),
        ("San Francisco Bay Area", "America/Los_Angeles"),
        ("Berlin, Germany", "Europe/Berlin"),
        ("Sofia, Bulgaria", "Europe/Sofia"),
        ("Cape Town, South Africa", "Africa/Johannesburg"),
        ("Bengaluru, Karnataka, India", "Asia/Kolkata"),
        ("Sydney, Australia", "Australia/Sydney"),
        ("Somewhere unknown", "Europe/Paris"),
        (None, "Europe/Paris"),
        ("", "Europe/Paris"),
    ],
)
def test_guess_timezone(loc, tz):
    assert guess_timezone(loc, "Europe/Paris") == tz


def test_resolve_tz_falls_back():
    assert str(resolve_tz("Not/AZone", "Europe/Sofia")) == "Europe/Sofia"
    assert str(resolve_tz(None, "Also/Bad")) == "UTC"
    assert str(resolve_tz("Asia/Tokyo")) == "Asia/Tokyo"


def test_send_window_inside_tuesday_morning():
    # Tuesday 2026-09-08 09:00 UTC
    now = dt(2026, 9, 8, 9, 0)
    open_at, close_at = next_window("send", now, ZoneInfo("UTC"))
    assert open_at == dt(2026, 9, 8, 8, 30) and close_at == dt(2026, 9, 8, 11, 0)
    assert in_window("send", now, ZoneInfo("UTC"))


def test_send_window_skips_to_afternoon_then_next_day():
    now = dt(2026, 9, 8, 12, 0)  # Tuesday noon UTC
    assert next_window("send", now, ZoneInfo("UTC")) == (
        dt(2026, 9, 8, 14, 0),
        dt(2026, 9, 8, 16, 0),
    )
    late = dt(2026, 9, 8, 17, 0)
    assert next_window("send", late, ZoneInfo("UTC"))[0] == dt(2026, 9, 9, 8, 30)


def test_send_window_skips_friday_and_weekend_to_tuesday():
    friday = dt(2026, 9, 11, 9, 0)
    open_at, _ = next_window("send", friday, ZoneInfo("UTC"))
    assert open_at == dt(2026, 9, 15, 8, 30)  # next Tuesday


def test_engage_window_weekdays_only():
    saturday = dt(2026, 9, 12, 10, 0)
    open_at, close_at = next_window("engage", saturday, ZoneInfo("UTC"))
    assert open_at == dt(2026, 9, 14, 9, 0) and close_at == dt(2026, 9, 14, 18, 0)


def test_any_window_includes_saturday():
    saturday = dt(2026, 9, 12, 10, 0)
    assert in_window("any", saturday, ZoneInfo("UTC"))
    sunday = dt(2026, 9, 13, 10, 0)
    assert next_window("any", sunday, ZoneInfo("UTC"))[0] == dt(2026, 9, 14, 8, 0)


def test_window_is_in_recipient_local_time():
    # Tuesday 09:00 UTC is 05:00 in New York: not yet in the send window there.
    now = dt(2026, 9, 8, 9, 0)
    open_at, _ = next_window("send", now, NY)
    assert open_at == datetime(2026, 9, 8, 8, 30, tzinfo=NY).astimezone(UTC)
    assert not in_window("send", now, NY)


def test_schedule_in_window_not_before_is_max_of_open_and_earliest():
    inside = dt(2026, 9, 8, 9, 15)
    nb, na = schedule_in_window("send", inside, ZoneInfo("UTC"))
    assert nb == inside and na == dt(2026, 9, 8, 11, 0)
    before = dt(2026, 9, 8, 7, 0)
    nb, na = schedule_in_window("send", before, ZoneInfo("UTC"))
    assert nb == dt(2026, 9, 8, 8, 30)


def test_dst_transition_does_not_break_windows():
    # US DST ends 2026-11-01; Monday 2 Nov 13:00 UTC is 08:00 NY.
    now = dt(2026, 11, 2, 13, 0)
    open_at, _ = next_window("engage", now, NY)
    assert open_at == datetime(2026, 11, 2, 9, 0, tzinfo=NY).astimezone(UTC)
    assert open_at.hour == 14  # 09:00 EST = 14:00 UTC
