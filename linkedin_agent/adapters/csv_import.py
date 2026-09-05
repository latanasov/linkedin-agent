"""CSV -> LeadRecord list."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.prompts import LINKEDIN_URL_RE
from ..core.timezone import guess_timezone
from ..models import LeadRecord

KNOWN_COLUMNS = {
    "linkedin_url",
    "url",
    "profile_url",
    "first_name",
    "last_name",
    "name",
    "company",
    "title",
    "email",
    "location",
    "timezone",
}


@dataclass
class ImportResult:
    leads: list[LeadRecord] = field(default_factory=list)
    skipped: list[tuple[int, str]] = field(default_factory=list)  # (row number, reason)
    custom_columns: set[str] = field(default_factory=set)


def _normalise_url(url: str) -> str:
    url = url.strip()
    if url.startswith("linkedin.com") or url.startswith("www.linkedin.com"):
        url = "https://" + url
    if url.startswith("http://"):
        url = "https://" + url[len("http://") :]
    return url.split("?")[0].rstrip("/") + "/" if LINKEDIN_URL_RE.match(url) else url


def parse_rows(
    rows: Iterable[Mapping[str, Any]], campaign: str, default_tz: str = "UTC"
) -> ImportResult:
    """Turn dict rows (from a CSV, an API result, or a chat) into leads.

    Keys are case-insensitive. One of linkedin_url / url / profile_url is required per row;
    every other known column is optional; unknown columns become custom fields."""
    result = ImportResult()
    seen: set[str] = set()
    for i, raw in enumerate(rows, start=2):
        row = {
            str(k or "").strip().lower(): str(v if v is not None else "").strip()
            for k, v in raw.items()
        }
        result.custom_columns |= {k for k in row if k not in KNOWN_COLUMNS and k}
        url = _normalise_url(
            row.get("linkedin_url") or row.get("url") or row.get("profile_url") or ""
        )
        if not LINKEDIN_URL_RE.match(url):
            result.skipped.append((i, f"invalid LinkedIn URL: {url[:60]!r}"))
            continue
        if url in seen:
            result.skipped.append((i, "duplicate URL in file"))
            continue
        seen.add(url)
        first, last = row.get("first_name", ""), row.get("last_name", "")
        if not first and row.get("name"):
            parts = row["name"].split(" ", 1)
            first, last = parts[0], (parts[1] if len(parts) > 1 else last)
        custom = {k: v for k, v in row.items() if k in result.custom_columns and v}
        tz = row.get("timezone") or guess_timezone(row.get("location"), default_tz)
        result.leads.append(
            LeadRecord(
                campaign=campaign,
                linkedin_url=url,
                first_name=first or None,
                last_name=last or None,
                company=row.get("company") or None,
                title=row.get("title") or None,
                email=row.get("email") or None,
                location=row.get("location") or None,
                timezone=tz,
                custom_fields=custom,
            )
        )
    return result


def parse_leads(path: Path, campaign: str, default_tz: str = "UTC") -> ImportResult:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = [h.strip().lower() for h in (reader.fieldnames or [])]
        if not any(h in ("linkedin_url", "url", "profile_url") for h in headers):
            raise ValueError("CSV needs a 'linkedin_url' column")
        result = parse_rows(list(reader), campaign, default_tz)
    result.custom_columns |= {h for h in headers if h not in KNOWN_COLUMNS and h}
    return result
