"""Recipient time zones and send windows. Pure."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


@dataclass(frozen=True)
class WindowSpec:
    weekdays: frozenset[int]  # Monday=0
    slots: tuple[tuple[time, time], ...]


WINDOWS: dict[str, WindowSpec] = {
    # Invites and messages: Tue–Thu, mid-morning and mid-afternoon.
    "send": WindowSpec(
        frozenset({1, 2, 3}), ((time(8, 30), time(11, 0)), (time(14, 0), time(16, 0)))
    ),
    # Warm-up touches: any weekday working hours.
    "engage": WindowSpec(frozenset({0, 1, 2, 3, 4}), ((time(9, 0), time(18, 0)),)),
    # Read-only checks and visits: Mon–Sat, long window.
    "any": WindowSpec(frozenset({0, 1, 2, 3, 4, 5}), ((time(8, 0), time(20, 0)),)),
}

BUILTIN_WINDOW_NAMES = frozenset(WINDOWS)

MAX_LOOKAHEAD_DAYS = 14

DAY_NAMES: dict[str, int] = {
    name: i
    for i, names in enumerate(
        (
            ("mon", "monday"),
            ("tue", "tuesday", "tues"),
            ("wed", "wednesday"),
            ("thu", "thursday", "thur", "thurs"),
            ("fri", "friday"),
            ("sat", "saturday"),
            ("sun", "sunday"),
        )
    )
    for name in names
}

_SLOT_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


def parse_days(values: Sequence[str | int]) -> frozenset[int]:
    """Weekday numbers (Monday=0) from names like 'tue' or numbers."""
    days: set[int] = set()
    for v in values:
        if isinstance(v, int) or (isinstance(v, str) and v.strip().isdigit()):
            n = int(v)
            if not 0 <= n <= 6:
                raise ValueError(f"Day {v!r} out of range; use 0 (Monday) to 6 (Sunday)")
            days.add(n)
            continue
        key = str(v).strip().lower()
        if key not in DAY_NAMES:
            raise ValueError(f"Unknown day {v!r}; use mon, tue, wed, thu, fri, sat or sun")
        days.add(DAY_NAMES[key])
    if not days:
        raise ValueError("A window needs at least one day")
    return frozenset(days)


def parse_slots(values: Sequence[str]) -> tuple[tuple[time, time], ...]:
    """Time ranges from strings like '08:30-11:00'. Each must open before it closes."""
    slots: list[tuple[time, time]] = []
    for v in values:
        m = _SLOT_RE.match(str(v))
        if not m:
            raise ValueError(f"Bad hours {v!r}; use 'HH:MM-HH:MM', e.g. '08:30-11:00'")
        h1, m1, h2, m2 = (int(g) for g in m.groups())
        for h, mi in ((h1, m1), (h2, m2)):
            if h > 23 or mi > 59:
                raise ValueError(f"Bad hours {v!r}; hours are 00-23 and minutes 00-59")
        start, end = time(h1, m1), time(h2, m2)
        if start >= end:
            raise ValueError(f"Window {v!r} opens at or after it closes")
        slots.append((start, end))
    if not slots:
        raise ValueError("A window needs at least one time range")
    return tuple(slots)


def describe_window(spec: WindowSpec) -> str:
    """'Tue\u2013Thu 08:30-11:00, 14:00-16:00', for `campaign show` and error messages.

    The week is read as a circle, starting after its longest gap, so a Sunday-to-Thursday
    working week prints as 'Sun\u2013Thu' rather than 'Mon\u2013Thu/Sun'."""
    short = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    days = sorted(spec.weekdays)
    gaps = [(days[(i + 1) % len(days)] - d) % 7 for i, d in enumerate(days)]
    # Break after the last of the widest gaps, so an unbroken week still starts at Monday.
    start_at = (max(range(len(gaps)), key=lambda i: (gaps[i], i)) + 1) % len(days)
    ordered = days[start_at:] + days[:start_at]

    runs: list[str] = []
    first = prev = ordered[0]
    for d in [*ordered[1:], None]:
        if d is not None and d == (prev + 1) % 7:
            prev = d
            continue
        runs.append(short[first] if first == prev else f"{short[first]}\u2013{short[prev]}")
        if d is not None:
            first = prev = d
    hours = ", ".join(f"{a:%H:%M}-{b:%H:%M}" for a, b in spec.slots)
    return f"{'/'.join(runs)} {hours}"


def resolve_tz(name: str | None, default: str = "UTC") -> ZoneInfo:
    for candidate in (name, default, "UTC"):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo("UTC")


def window_spec(kind: str, windows: Mapping[str, WindowSpec] | None = None) -> WindowSpec:
    """The spec for `kind`: a campaign's own window first, then the three built-ins."""
    if windows and kind in windows:
        return windows[kind]
    try:
        return WINDOWS[kind]
    except KeyError:
        known = sorted({*WINDOWS, *(windows or {})})
        raise ValueError(f"Unknown window {kind!r}; known windows: {', '.join(known)}") from None


def next_window(
    kind: str,
    now: datetime,
    tz: ZoneInfo,
    windows: Mapping[str, WindowSpec] | None = None,
) -> tuple[datetime, datetime]:
    """Earliest (open, close) in UTC of the window of `kind` whose close is after `now`.

    If `now` is inside a window the returned open is that window's open (which is <= now).
    """
    spec = window_spec(kind, windows)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    local_now = now.astimezone(tz)
    for day_offset in range(MAX_LOOKAHEAD_DAYS + 1):
        day = (local_now + timedelta(days=day_offset)).date()
        if day.weekday() not in spec.weekdays:
            continue
        for start, end in spec.slots:
            open_local = datetime.combine(day, start, tzinfo=tz)
            close_local = datetime.combine(day, end, tzinfo=tz)
            if close_local > local_now:
                return open_local.astimezone(UTC), close_local.astimezone(UTC)
    raise RuntimeError(f"No {kind} window within {MAX_LOOKAHEAD_DAYS} days")


def in_window(
    kind: str, now: datetime, tz: ZoneInfo, windows: Mapping[str, WindowSpec] | None = None
) -> bool:
    open_at, close_at = next_window(kind, now, tz, windows)
    return open_at <= now < close_at


def schedule_in_window(
    kind: str,
    earliest: datetime,
    tz: ZoneInfo,
    windows: Mapping[str, WindowSpec] | None = None,
) -> tuple[datetime, datetime]:
    """(not_before, not_after) for a task that may run no earlier than `earliest`."""
    open_at, close_at = next_window(kind, earliest, tz, windows)
    return max(open_at, earliest), close_at


# Location keyword -> IANA zone. Deliberately small; the campaign default covers the rest.
_LOCATION_TZ: list[tuple[str, str]] = [
    # United States
    ("new york", "America/New_York"),
    ("boston", "America/New_York"),
    ("miami", "America/New_York"),
    ("atlanta", "America/New_York"),
    ("washington", "America/New_York"),
    ("philadelphia", "America/New_York"),
    ("chicago", "America/Chicago"),
    ("austin", "America/Chicago"),
    ("dallas", "America/Chicago"),
    ("houston", "America/Chicago"),
    ("minneapolis", "America/Chicago"),
    ("denver", "America/Denver"),
    ("phoenix", "America/Phoenix"),
    ("salt lake", "America/Denver"),
    ("san francisco", "America/Los_Angeles"),
    ("los angeles", "America/Los_Angeles"),
    ("seattle", "America/Los_Angeles"),
    ("portland", "America/Los_Angeles"),
    ("san diego", "America/Los_Angeles"),
    ("bay area", "America/Los_Angeles"),
    ("california", "America/Los_Angeles"),
    ("texas", "America/Chicago"),
    ("florida", "America/New_York"),
    ("illinois", "America/Chicago"),
    ("massachusetts", "America/New_York"),
    ("colorado", "America/Denver"),
    ("united states", "America/New_York"),
    ("usa", "America/New_York"),
    # Canada
    ("toronto", "America/Toronto"),
    ("montreal", "America/Toronto"),
    ("ottawa", "America/Toronto"),
    ("vancouver", "America/Vancouver"),
    ("calgary", "America/Edmonton"),
    ("canada", "America/Toronto"),
    # Latin America
    ("sao paulo", "America/Sao_Paulo"),
    ("são paulo", "America/Sao_Paulo"),
    ("brazil", "America/Sao_Paulo"),
    ("mexico", "America/Mexico_City"),
    ("buenos aires", "America/Argentina/Buenos_Aires"),
    ("argentina", "America/Argentina/Buenos_Aires"),
    ("bogota", "America/Bogota"),
    ("colombia", "America/Bogota"),
    ("santiago", "America/Santiago"),
    ("chile", "America/Santiago"),
    ("lima", "America/Lima"),
    ("peru", "America/Lima"),
    # UK / Ireland
    ("london", "Europe/London"),
    ("manchester", "Europe/London"),
    ("edinburgh", "Europe/London"),
    ("united kingdom", "Europe/London"),
    ("england", "Europe/London"),
    ("scotland", "Europe/London"),
    ("dublin", "Europe/Dublin"),
    ("ireland", "Europe/Dublin"),
    ("lisbon", "Europe/Lisbon"),
    ("portugal", "Europe/Lisbon"),
    # Central Europe
    ("berlin", "Europe/Berlin"),
    ("munich", "Europe/Berlin"),
    ("hamburg", "Europe/Berlin"),
    ("germany", "Europe/Berlin"),
    ("paris", "Europe/Paris"),
    ("france", "Europe/Paris"),
    ("amsterdam", "Europe/Amsterdam"),
    ("netherlands", "Europe/Amsterdam"),
    ("brussels", "Europe/Brussels"),
    ("belgium", "Europe/Brussels"),
    ("zurich", "Europe/Zurich"),
    ("switzerland", "Europe/Zurich"),
    ("vienna", "Europe/Vienna"),
    ("austria", "Europe/Vienna"),
    ("madrid", "Europe/Madrid"),
    ("barcelona", "Europe/Madrid"),
    ("spain", "Europe/Madrid"),
    ("milan", "Europe/Rome"),
    ("rome", "Europe/Rome"),
    ("italy", "Europe/Rome"),
    ("stockholm", "Europe/Stockholm"),
    ("sweden", "Europe/Stockholm"),
    ("oslo", "Europe/Oslo"),
    ("norway", "Europe/Oslo"),
    ("copenhagen", "Europe/Copenhagen"),
    ("denmark", "Europe/Copenhagen"),
    ("warsaw", "Europe/Warsaw"),
    ("poland", "Europe/Warsaw"),
    ("prague", "Europe/Prague"),
    ("czech", "Europe/Prague"),
    ("budapest", "Europe/Budapest"),
    ("hungary", "Europe/Budapest"),
    # Eastern Europe
    ("sofia", "Europe/Sofia"),
    ("bulgaria", "Europe/Sofia"),
    ("bucharest", "Europe/Bucharest"),
    ("romania", "Europe/Bucharest"),
    ("athens", "Europe/Athens"),
    ("greece", "Europe/Athens"),
    ("helsinki", "Europe/Helsinki"),
    ("finland", "Europe/Helsinki"),
    ("kyiv", "Europe/Kyiv"),
    ("kiev", "Europe/Kyiv"),
    ("ukraine", "Europe/Kyiv"),
    ("istanbul", "Europe/Istanbul"),
    ("turkey", "Europe/Istanbul"),
    ("tallinn", "Europe/Tallinn"),
    ("riga", "Europe/Riga"),
    ("vilnius", "Europe/Vilnius"),
    # Middle East / Africa
    ("tel aviv", "Asia/Jerusalem"),
    ("israel", "Asia/Jerusalem"),
    ("dubai", "Asia/Dubai"),
    ("united arab emirates", "Asia/Dubai"),
    ("uae", "Asia/Dubai"),
    ("riyadh", "Asia/Riyadh"),
    ("saudi", "Asia/Riyadh"),
    ("cairo", "Africa/Cairo"),
    ("egypt", "Africa/Cairo"),
    ("lagos", "Africa/Lagos"),
    ("nigeria", "Africa/Lagos"),
    ("nairobi", "Africa/Nairobi"),
    ("kenya", "Africa/Nairobi"),
    ("johannesburg", "Africa/Johannesburg"),
    ("cape town", "Africa/Johannesburg"),
    ("south africa", "Africa/Johannesburg"),
    # Asia-Pacific
    ("mumbai", "Asia/Kolkata"),
    ("bangalore", "Asia/Kolkata"),
    ("bengaluru", "Asia/Kolkata"),
    ("delhi", "Asia/Kolkata"),
    ("hyderabad", "Asia/Kolkata"),
    ("pune", "Asia/Kolkata"),
    ("chennai", "Asia/Kolkata"),
    ("india", "Asia/Kolkata"),
    ("singapore", "Asia/Singapore"),
    ("kuala lumpur", "Asia/Kuala_Lumpur"),
    ("malaysia", "Asia/Kuala_Lumpur"),
    ("jakarta", "Asia/Jakarta"),
    ("indonesia", "Asia/Jakarta"),
    ("bangkok", "Asia/Bangkok"),
    ("thailand", "Asia/Bangkok"),
    ("manila", "Asia/Manila"),
    ("philippines", "Asia/Manila"),
    ("hong kong", "Asia/Hong_Kong"),
    ("shanghai", "Asia/Shanghai"),
    ("beijing", "Asia/Shanghai"),
    ("shenzhen", "Asia/Shanghai"),
    ("china", "Asia/Shanghai"),
    ("taipei", "Asia/Taipei"),
    ("taiwan", "Asia/Taipei"),
    ("seoul", "Asia/Seoul"),
    ("korea", "Asia/Seoul"),
    ("tokyo", "Asia/Tokyo"),
    ("osaka", "Asia/Tokyo"),
    ("japan", "Asia/Tokyo"),
    ("sydney", "Australia/Sydney"),
    ("melbourne", "Australia/Melbourne"),
    ("brisbane", "Australia/Brisbane"),
    ("perth", "Australia/Perth"),
    ("australia", "Australia/Sydney"),
    ("auckland", "Pacific/Auckland"),
    ("wellington", "Pacific/Auckland"),
    ("new zealand", "Pacific/Auckland"),
]


def guess_timezone(location: str | None, default: str = "UTC") -> str:
    """Best-effort IANA zone from a free-text LinkedIn location; falls back to `default`."""
    if not location:
        return default
    loc = location.lower()
    # Longer keys first so "new york" beats "york", "south africa" beats "africa".
    for key, tz in sorted(_LOCATION_TZ, key=lambda kv: -len(kv[0])):
        if key in loc:
            return tz
    return default
