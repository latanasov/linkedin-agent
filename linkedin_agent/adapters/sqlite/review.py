from __future__ import annotations

from datetime import datetime
from typing import Any

from ...models import ReviewItem
from .db import Database, dumps, iso, loads, parse_dt


class SqliteReviewQueue:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def submit(self, task_id: str, kind: str, context: dict[str, Any], draft: str) -> None:
        await self._db.execute(
            """INSERT INTO review_queue(task_id, kind, context, draft) VALUES (?,?,?,?)
               ON CONFLICT(task_id) DO UPDATE SET kind=excluded.kind, context=excluded.context,
                 draft=excluded.draft, approved_text=NULL, decided_at=NULL""",
            (task_id, kind, dumps(context), draft),
        )
        await self._db.commit()

    async def pending(self) -> list[ReviewItem]:
        rows = await self._db.fetchall(
            "SELECT * FROM review_queue WHERE decided_at IS NULL ORDER BY rowid"
        )
        return [
            ReviewItem(
                task_id=r["task_id"],
                kind=r["kind"],
                context=loads(r["context"], {}),
                draft=r["draft"],
                approved_text=r["approved_text"],
                decided_at=parse_dt(r["decided_at"]),
            )
            for r in rows
        ]

    async def get(self, task_id: str) -> ReviewItem | None:
        r = await self._db.fetchone("SELECT * FROM review_queue WHERE task_id=?", (task_id,))
        if not r:
            return None
        return ReviewItem(
            task_id=r["task_id"],
            kind=r["kind"],
            context=loads(r["context"], {}),
            draft=r["draft"],
            approved_text=r["approved_text"],
            decided_at=parse_dt(r["decided_at"]),
        )

    async def decide(self, task_id: str, approved_text: str | None, at: datetime) -> None:
        await self._db.execute(
            "UPDATE review_queue SET approved_text=?, decided_at=? WHERE task_id=?",
            (approved_text, iso(at), task_id),
        )
        await self._db.commit()
