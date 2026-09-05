"""Recipient time zones and send windows. Pure."""

from __future__ import annotations

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

MAX_LOOKAHEAD_DAYS = 14


def resolve_tz(name: str | None, default: str = "UTC") -> ZoneInfo:
    for candidate in (name, default, "UTC"):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo("UTC")


def next_window(kind: str, now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Earliest (open, close) in UTC of the window of `kind` whose close is after `now`.

    If `now` is inside a window the returned open is that window's open (which is <= now).
    """
    spec = WINDOWS[kind]
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


def in_window(kind: str, now: datetime, tz: ZoneInfo) -> bool:
    open_at, close_at = next_window(kind, now, tz)
    return open_at <= now < close_at


def schedule_in_window(kind: str, earliest: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """(not_before, not_after) for a task that may run no earlier than `earliest`."""
    open_at, close_at = next_window(kind, earliest, tz)
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
