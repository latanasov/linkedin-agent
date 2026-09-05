from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from linkedin_agent.core.timezone import (
    describe_window,
    guess_timezone,
    in_window,
    next_window,
    parse_days,
    parse_slots,
    resolve_tz,
    schedule_in_window,
    window_spec,
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


# ── campaign-defined windows ───────────────────────────────────────────────


def test_parse_days_takes_names_and_numbers():
    assert parse_days(["mon", "Tue", "SUN"]) == frozenset({0, 1, 6})
    assert parse_days([0, "3", 6]) == frozenset({0, 3, 6})
    for bad in (["funday"], [7], ["-1"], []):
        with pytest.raises(ValueError):
            parse_days(bad)


def test_parse_slots_wants_hh_mm_ranges_that_open_before_they_close():
    from datetime import time

    assert parse_slots(["08:30-11:00", "14:00-16:00"]) == (
        (time(8, 30), time(11, 0)),
        (time(14, 0), time(16, 0)),
    )
    for bad in (["8-11"], ["18:00-09:00"], ["10:00-10:00"], ["25:00-26:00"], []):
        with pytest.raises(ValueError):
            parse_slots(bad)


def test_window_spec_prefers_the_campaign_then_the_built_ins():
    from linkedin_agent.core.timezone import WindowSpec

    own = WindowSpec(parse_days(["sun"]), parse_slots(["09:00-10:00"]))
    assert window_spec("send", {"gulf": own}) is not own, "built-in send untouched"
    assert window_spec("gulf", {"gulf": own}) is own
    assert window_spec("send", {"send": own}) is own, "a campaign may redefine a built-in"
    with pytest.raises(ValueError, match="Unknown window 'nope'"):
        window_spec("nope", {"gulf": own})


def test_a_campaign_window_schedules_on_its_own_days():
    """A Sunday-to-Thursday week: Saturday's next slot is Sunday, not Tuesday."""
    from linkedin_agent.core.timezone import WindowSpec

    gulf = {
        "gulf": WindowSpec(
            parse_days(["sun", "mon", "tue", "wed", "thu"]), parse_slots(["09:00-12:00"])
        )
    }
    dubai = ZoneInfo("Asia/Dubai")
    saturday = dt(2026, 9, 5, 10, 0)
    open_at, close_at = schedule_in_window("gulf", saturday, dubai, gulf)
    assert open_at == dt(2026, 9, 6, 5, 0) and close_at == dt(2026, 9, 6, 8, 0)
    # the built-in send window from the same moment lands on Tuesday instead
    assert schedule_in_window("send", saturday, dubai, gulf)[0] == dt(2026, 9, 8, 4, 30)
    assert in_window("gulf", dt(2026, 9, 6, 6, 0), dubai, gulf) is True
    assert in_window("gulf", saturday, dubai, gulf) is False


def test_describe_window_reads_as_days_and_hours():
    from linkedin_agent.core.timezone import WINDOWS

    assert describe_window(WINDOWS["send"]) == "Tue\u2013Thu 08:30-11:00, 14:00-16:00"
    assert describe_window(WINDOWS["engage"]) == "Mon\u2013Fri 09:00-18:00"
    one = WindowSpecOf(["sat"], ["10:00-11:00"])
    assert describe_window(one) == "Sat 10:00-11:00"
    split = WindowSpecOf(["mon", "wed", "thu", "fri"], ["09:00-10:00"])
    assert describe_window(split) == "Mon/Wed\u2013Fri 09:00-10:00"
    # the week is a circle: a Sunday-to-Thursday week reads from Sunday
    gulf = WindowSpecOf(["sun", "mon", "tue", "wed", "thu"], ["09:00-12:00"])
    assert describe_window(gulf) == "Sun\u2013Thu 09:00-12:00"
    weekend = WindowSpecOf(["sat", "sun"], ["10:00-14:00"])
    assert describe_window(weekend) == "Sat\u2013Sun 10:00-14:00"
    every_day = WindowSpecOf(["mon", "tue", "wed", "thu", "fri", "sat", "sun"], ["09:00-10:00"])
    assert describe_window(every_day) == "Mon\u2013Sun 09:00-10:00"


def WindowSpecOf(days, hours):
    from linkedin_agent.core.timezone import WindowSpec

    return WindowSpec(parse_days(days), parse_slots(hours))
