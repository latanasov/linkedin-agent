from __future__ import annotations

from datetime import datetime

import aiosqlite

from ...models import LeadRecord, LeadSequence, LeadStage, PostRef
from .db import Database, dumps, iso, loads, parse_dt


def _row_to_lead(row: aiosqlite.Row) -> LeadRecord:
    return LeadRecord(
        id=row["id"],
        campaign=row["campaign"],
        linkedin_url=row["linkedin_url"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        company=row["company"],
        title=row["title"],
        email=row["email"],
        location=row["location"],
        timezone=row["timezone"],
        custom_fields=loads(row["custom_fields"], {}),
        profile=loads(row["profile"], {}),
        posts=[PostRef(**p) for p in loads(row["posts"], [])],
        stage=LeadStage(row["stage"]),
        invited_at=parse_dt(row["invited_at"]),
        connected_at=parse_dt(row["connected_at"]),
        last_touch_at=parse_dt(row["last_touch_at"]),
        last_message_at=parse_dt(row["last_message_at"]),
        last_message_text=row["last_message_text"],
        prior_reply_text=row["prior_reply_text"],
        replied_at=parse_dt(row["replied_at"]),
        created_at=parse_dt(row["created_at"]),
    )


def _row_to_seq(row: aiosqlite.Row) -> LeadSequence:
    return LeadSequence(
        lead_id=row["lead_id"],
        campaign=row["campaign"],
        step_id=row["step_id"],
        branch=row["branch"],
        next_due_at=parse_dt(row["next_due_at"]),
        step_entered_at=parse_dt(row["step_entered_at"]),
        history=loads(row["history"], []),
    )


_LEAD_COLS = (
    "id, campaign, linkedin_url, first_name, last_name, company, title, email, location, timezone, "
    "custom_fields, profile, posts, stage, invited_at, connected_at, last_touch_at, last_message_at, "
    "last_message_text, prior_reply_text, replied_at, created_at"
)


def _lead_values(lead: LeadRecord, now: datetime) -> tuple[object, ...]:
    return (
        lead.id,
        lead.campaign,
        lead.linkedin_url,
        lead.first_name,
        lead.last_name,
        lead.company,
        lead.title,
        lead.email,
        lead.location,
        lead.timezone,
        dumps(lead.custom_fields),
        dumps(lead.profile),
        dumps([p.model_dump() for p in lead.posts]),
        lead.stage.value,
        iso(lead.invited_at),
        iso(lead.connected_at),
        iso(lead.last_touch_at),
        iso(lead.last_message_at),
        lead.last_message_text,
        lead.prior_reply_text,
        iso(lead.replied_at),
        iso(lead.created_at or now),
    )


class SqliteLeadStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, lead_id: str) -> LeadRecord | None:
        row = await self._db.fetchone("SELECT * FROM leads WHERE id=?", (lead_id,))
        return _row_to_lead(row) if row else None

    async def find(self, key: str) -> LeadRecord | None:
        """Look a lead up by id, full URL, or the /in/<slug> part of the URL."""
        key = key.strip()
        row = await self._db.fetchone(
            "SELECT * FROM leads WHERE id=? OR linkedin_url=?", (key, key)
        )
        if row is None:
            slug = key.rstrip("/").split("/")[-1]
            row = await self._db.fetchone(
                "SELECT * FROM leads WHERE linkedin_url LIKE ? OR linkedin_url LIKE ? LIMIT 1",
                (f"%/in/{slug}/%", f"%/in/{slug}"),
            )
        if row is None:
            row = await self._db.fetchone(
                "SELECT * FROM leads WHERE lower(first_name||' '||coalesce(last_name,''))=lower(?) LIMIT 1",
                (key,),
            )
        return _row_to_lead(row) if row else None

    async def upsert_many(self, leads: list[LeadRecord]) -> tuple[int, int]:
        """Insert new leads by URL, update name/company/title/custom fields of existing ones.
        Returns (inserted, updated)."""
        inserted = updated = 0
        now = datetime.now().astimezone()
        for lead in leads:
            existing = await self._db.fetchone(
                "SELECT id FROM leads WHERE linkedin_url=?", (lead.linkedin_url,)
            )
            if existing:
                await self._db.execute(
                    """UPDATE leads SET first_name=coalesce(?, first_name), last_name=coalesce(?, last_name),
                       company=coalesce(?, company), title=coalesce(?, title), email=coalesce(?, email),
                       location=coalesce(?, location), timezone=coalesce(timezone, ?),
                       custom_fields=? WHERE id=?""",
                    (
                        lead.first_name,
                        lead.last_name,
                        lead.company,
                        lead.title,
                        lead.email,
                        lead.location,
                        lead.timezone,
                        dumps(lead.custom_fields),
                        existing["id"],
                    ),
                )
                lead.id = existing["id"]
                updated += 1
            else:
                await self._db.execute(
                    f"INSERT INTO leads({_LEAD_COLS}) VALUES ({','.join('?' * 22)})",
                    _lead_values(lead, now),
                )
                inserted += 1
        await self._db.commit()
        return inserted, updated

    async def update(self, lead: LeadRecord) -> None:
        vals = _lead_values(lead, datetime.now().astimezone())
        await self._db.execute(
            """UPDATE leads SET campaign=?, linkedin_url=?, first_name=?, last_name=?, company=?, title=?,
               email=?, location=?, timezone=?, custom_fields=?, profile=?, posts=?, stage=?, invited_at=?,
               connected_at=?, last_touch_at=?, last_message_at=?, last_message_text=?, prior_reply_text=?, replied_at=?,
               created_at=? WHERE id=?""",
            (*vals[1:], lead.id),
        )
        await self._db.commit()

    async def delete(self, lead_id: str) -> None:
        await self._db.execute("DELETE FROM leads WHERE id=?", (lead_id,))
        await self._db.commit()

    # ── sequences ────────────────────────────────────────────────────────

    async def get_sequence(self, lead_id: str) -> LeadSequence | None:
        row = await self._db.fetchone("SELECT * FROM lead_sequences WHERE lead_id=?", (lead_id,))
        return _row_to_seq(row) if row else None

    async def save_sequence(self, seq: LeadSequence) -> None:
        await self._db.execute(
            """INSERT INTO lead_sequences(lead_id, campaign, step_id, branch, next_due_at, step_entered_at, history)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(lead_id) DO UPDATE SET campaign=excluded.campaign, step_id=excluded.step_id,
                 branch=excluded.branch, next_due_at=excluded.next_due_at,
                 step_entered_at=excluded.step_entered_at, history=excluded.history""",
            (
                seq.lead_id,
                seq.campaign,
                seq.step_id,
                seq.branch,
                iso(seq.next_due_at),
                iso(seq.step_entered_at),
                dumps(seq.history[-200:]),
            ),
        )
        await self._db.commit()

    async def due_sequences(
        self, now: datetime, campaign: str | None = None
    ) -> list[tuple[LeadRecord, LeadSequence]]:
        sql = """SELECT l.*, s.lead_id AS s_lead_id, s.campaign AS s_campaign, s.step_id, s.branch,
                        s.next_due_at, s.step_entered_at, s.history
                 FROM lead_sequences s JOIN leads l ON l.id=s.lead_id
                 WHERE s.step_id IS NOT NULL AND s.next_due_at IS NOT NULL AND s.next_due_at<=?
                   AND s.paused=0 AND l.stage NOT IN ('paused','replied','done')"""
        params: list[object] = [iso(now)]
        if campaign:
            sql += " AND s.campaign=?"
            params.append(campaign)
        sql += " ORDER BY s.next_due_at"
        rows = await self._db.fetchall(sql, tuple(params))
        out: list[tuple[LeadRecord, LeadSequence]] = []
        for r in rows:
            lead = _row_to_lead(r)
            seq = LeadSequence(
                lead_id=r["s_lead_id"],
                campaign=r["s_campaign"],
                step_id=r["step_id"],
                branch=r["branch"],
                next_due_at=parse_dt(r["next_due_at"]),
                step_entered_at=parse_dt(r["step_entered_at"]),
                history=loads(r["history"], []),
            )
            out.append((lead, seq))
        return out

    # ── queries ──────────────────────────────────────────────────────────

    async def by_stage(self, stage: LeadStage, campaign: str | None = None) -> list[LeadRecord]:
        sql, params = "SELECT * FROM leads WHERE stage=?", [stage.value]
        if campaign:
            sql += " AND campaign=?"
            params.append(campaign)
        rows = await self._db.fetchall(sql + " ORDER BY created_at", tuple(params))
        return [_row_to_lead(r) for r in rows]

    async def all(self, campaign: str | None = None) -> list[LeadRecord]:
        if campaign:
            rows = await self._db.fetchall(
                "SELECT * FROM leads WHERE campaign=? ORDER BY created_at", (campaign,)
            )
        else:
            rows = await self._db.fetchall("SELECT * FROM leads ORDER BY created_at")
        return [_row_to_lead(r) for r in rows]

    async def stage_counts(self, campaign: str | None = None) -> dict[str, int]:
        if campaign:
            rows = await self._db.fetchall(
                "SELECT stage, COUNT(*) AS n FROM leads WHERE campaign=? GROUP BY stage",
                (campaign,),
            )
        else:
            rows = await self._db.fetchall("SELECT stage, COUNT(*) AS n FROM leads GROUP BY stage")
        return {str(r["stage"]): int(r["n"]) for r in rows}

    async def acceptance_sample(
        self, start: datetime, end: datetime, campaign: str | None = None
    ) -> tuple[int, int]:
        """(invited, accepted) among leads invited between start and end."""
        sql = "SELECT COUNT(*) AS invited, SUM(CASE WHEN connected_at IS NOT NULL THEN 1 ELSE 0 END) AS accepted FROM leads WHERE invited_at>=? AND invited_at<?"
        params: list[object] = [iso(start), iso(end)]
        if campaign:
            sql += " AND campaign=?"
            params.append(campaign)
        row = await self._db.fetchone(sql, tuple(params))
        if not row:
            return 0, 0
        return int(row["invited"] or 0), int(row["accepted"] or 0)

    async def pause_campaign(self, campaign: str) -> int:
        cur = await self._db.execute(
            "UPDATE lead_sequences SET paused=1 WHERE campaign=? AND step_id IS NOT NULL AND paused=0",
            (campaign,),
        )
        await self._db.commit()
        return cur.rowcount or 0

    async def resume_campaign(self, campaign: str, now: datetime) -> int:
        cur = await self._db.execute(
            """UPDATE lead_sequences SET paused=0, next_due_at=coalesce(next_due_at, ?)
               WHERE campaign=? AND step_id IS NOT NULL AND paused=1""",
            (iso(now), campaign),
        )
        await self._db.commit()
        return cur.rowcount or 0

    async def is_paused(self, campaign: str) -> bool:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS n FROM lead_sequences WHERE campaign=? AND paused=1", (campaign,)
        )
        return bool(row and int(row["n"]) > 0)

    async def lead_ids_for_campaign(self, campaign: str) -> list[str]:
        rows = await self._db.fetchall("SELECT id FROM leads WHERE campaign=?", (campaign,))
        return [str(r["id"]) for r in rows]

    async def set_stage_for_campaign(
        self, campaign: str, from_stages: list[LeadStage] | None, to: LeadStage
    ) -> int:
        if from_stages:
            marks = ",".join("?" * len(from_stages))
            cur = await self._db.execute(
                f"UPDATE leads SET stage=? WHERE campaign=? AND stage IN ({marks})",
                (to.value, campaign, *[s.value for s in from_stages]),
            )
        else:
            cur = await self._db.execute(
                "UPDATE leads SET stage=? WHERE campaign=?", (to.value, campaign)
            )
        await self._db.commit()
        return cur.rowcount or 0
