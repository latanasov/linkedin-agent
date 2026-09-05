from __future__ import annotations

from datetime import datetime
from typing import Any

from ...models import TOUCH_ACTIONS, Action
from .db import Database, iso


class SqliteActionLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(
        self,
        account: str,
        action: Action,
        lead_id: str | None,
        ok: bool,
        result_status: str | None,
        at: datetime,
    ) -> None:
        await self._db.execute(
            "INSERT INTO action_log(account, action, lead_id, at, ok, result_status) VALUES (?,?,?,?,?,?)",
            (account, action.value, lead_id, iso(at), 1 if ok else 0, result_status),
        )
        await self._db.commit()

    async def count(self, account: str, action: Action, since: datetime) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS n FROM action_log WHERE account=? AND action=? AND ok=1 AND at>=?",
            (account, action.value, iso(since)),
        )
        return int(row["n"]) if row else 0

    async def touches(self, lead_id: str, since: datetime) -> int:
        marks = ",".join("?" * len(TOUCH_ACTIONS))
        row = await self._db.fetchone(
            f"SELECT COUNT(*) AS n FROM action_log WHERE lead_id=? AND ok=1 AND at>=? AND action IN ({marks})",
            (lead_id, iso(since), *[a.value for a in TOUCH_ACTIONS]),
        )
        return int(row["n"]) if row else 0

    async def recent(self, account: str, action: Action | None, limit: int) -> list[dict[str, Any]]:
        if action:
            rows = await self._db.fetchall(
                "SELECT * FROM action_log WHERE account=? AND action=? ORDER BY at DESC LIMIT ?",
                (account, action.value, limit),
            )
        else:
            rows = await self._db.fetchall(
                "SELECT * FROM action_log WHERE account=? ORDER BY at DESC LIMIT ?",
                (account, limit),
            )
        return [dict(r) for r in rows]

    async def count_between(
        self, account: str, action: Action, start: datetime, end: datetime, ok_only: bool = True
    ) -> int:
        sql = "SELECT COUNT(*) AS n FROM action_log WHERE account=? AND action=? AND at>=? AND at<?"
        if ok_only:
            sql += " AND ok=1"
        row = await self._db.fetchone(sql, (account, action.value, iso(start), iso(end)))
        return int(row["n"]) if row else 0
