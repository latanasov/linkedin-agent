from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import aiosqlite

from ...core.proc import pid_alive
from ...models import Action, Task, TaskResult, TaskStatus
from .db import Database, dumps, iso, loads, parse_dt

# A running task whose claiming process is still alive is left alone this long at most;
# after that the pid is assumed to have been reused and the task is requeued anyway.
RUNNING_HARD_LIMIT_S = 24 * 3600

OPEN_STATUSES = (
    TaskStatus.QUEUED.value,
    TaskStatus.RUNNING.value,
    TaskStatus.AWAITING_REVIEW.value,
)


def _row_to_task(row: aiosqlite.Row) -> Task:
    return Task(
        id=row["id"],
        lead_id=row["lead_id"],
        step_id=row["step_id"],
        action=Action(row["action"]),
        profile_url=row["profile_url"],
        account=row["account"],
        params=loads(row["params"], {}),
        status=TaskStatus(row["status"]),
        attempts=row["attempts"],
        not_before=parse_dt(row["not_before"]),
        not_after=parse_dt(row["not_after"]),
        body_hash=row["body_hash"],
        result=loads(row["result"], None),
        created_at=parse_dt(row["created_at"]),
        started_at=parse_dt(row["started_at"]),
        finished_at=parse_dt(row["finished_at"]),
    )


class SqliteTaskQueue:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def enqueue(self, task: Task) -> None:
        await self._db.execute(
            """INSERT INTO tasks(id, lead_id, step_id, action, profile_url, account, params, status,
                                 attempts, not_before, not_after, body_hash, result, created_at,
                                 started_at, finished_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task.id,
                task.lead_id,
                task.step_id,
                task.action.value,
                task.profile_url,
                task.account,
                dumps(task.params),
                task.status.value,
                task.attempts,
                iso(task.not_before),
                iso(task.not_after),
                task.body_hash,
                dumps(task.result) if task.result is not None else None,
                iso(task.created_at or datetime.now().astimezone()),
                iso(task.started_at),
                iso(task.finished_at),
            ),
        )
        await self._db.commit()

    async def update(self, task: Task) -> None:
        await self._db.execute(
            """UPDATE tasks SET params=?, status=?, attempts=?, not_before=?, not_after=?, body_hash=?,
                                result=?, started_at=?, finished_at=? WHERE id=?""",
            (
                dumps(task.params),
                task.status.value,
                task.attempts,
                iso(task.not_before),
                iso(task.not_after),
                task.body_hash,
                dumps(task.result) if task.result is not None else None,
                iso(task.started_at),
                iso(task.finished_at),
                task.id,
            ),
        )
        await self._db.commit()

    async def get(self, task_id: str) -> Task | None:
        row = await self._db.fetchone("SELECT * FROM tasks WHERE id=?", (task_id,))
        return _row_to_task(row) if row else None

    async def claim_next(self, account: str, now: datetime) -> Task | None:
        now_s = iso(now)
        await self._db.execute("BEGIN IMMEDIATE")
        try:
            row = await self._db.fetchone(
                """SELECT * FROM tasks
                   WHERE account=? AND status='queued'
                     AND (not_before IS NULL OR not_before<=?)
                     AND (not_after IS NULL OR not_after>?)
                   ORDER BY created_at LIMIT 1""",
                (account, now_s, now_s),
            )
            if row is None:
                await self._db.commit()
                return None
            await self._db.execute(
                """UPDATE tasks SET status='running', started_at=?, attempts=attempts+1,
                                    claimed_by=? WHERE id=?""",
                (now_s, os.getpid(), row["id"]),
            )
            await self._db.commit()
        except Exception:
            await self._db.execute("ROLLBACK")
            raise
        task = _row_to_task(row)
        task.status = TaskStatus.RUNNING
        task.started_at = now
        task.attempts += 1
        return task

    async def claim(self, task_id: str, now: datetime) -> Task | None:
        """Claim one specific queued task (used by one-off commands)."""
        cur = await self._db.execute(
            """UPDATE tasks SET status='running', started_at=?, attempts=attempts+1, claimed_by=?
               WHERE id=? AND status='queued'""",
            (iso(now), os.getpid(), task_id),
        )
        await self._db.commit()
        if not cur.rowcount:
            return None
        return await self.get(task_id)

    async def finish(self, task_id: str, result: TaskResult | None, status: TaskStatus) -> None:
        await self._db.execute(
            "UPDATE tasks SET status=?, result=?, finished_at=? WHERE id=?",
            (
                status.value,
                dumps(result.model_dump(mode="json")) if result else None,
                iso(datetime.now().astimezone()),
                task_id,
            ),
        )
        await self._db.commit()

    async def expire_overdue(self, now: datetime) -> int:
        cur = await self._db.execute(
            """UPDATE tasks SET status='skipped', result=?, finished_at=?
               WHERE status='queued' AND not_after IS NOT NULL AND not_after<=?""",
            (dumps({"status": "window_missed"}), iso(now), iso(now)),
        )
        await self._db.commit()
        return cur.rowcount or 0

    async def requeue_stale_running(self, now: datetime, older_than_s: int) -> int:
        """Give back tasks that a process claimed and will never finish.

        A task whose claiming process is gone is requeued at once: a killed run loop, a
        laptop that lost power, a one-off command that was Ctrl-C'd. One whose process is
        alive is left alone however long it takes (a local model can need an hour), up to
        a hard limit that guards against a reused pid. Rows without a claiming pid (from
        before schema 3) fall back to the plain age rule."""
        rows = await self._db.fetchall(
            "SELECT id, started_at, claimed_by FROM tasks WHERE status='running'"
        )
        stale: list[str] = []
        for r in rows:
            started = parse_dt(r["started_at"])
            age = (now - started).total_seconds() if started else float("inf")
            pid = r["claimed_by"]
            if pid is None:
                if age >= older_than_s:
                    stale.append(r["id"])
            elif not pid_alive(int(pid)) or age >= RUNNING_HARD_LIMIT_S:
                stale.append(r["id"])
        if not stale:
            return 0
        marks = ",".join("?" * len(stale))
        cur = await self._db.execute(
            f"""UPDATE tasks SET status='queued', started_at=NULL, claimed_by=NULL
                WHERE status='running' AND id IN ({marks})""",
            (*stale,),
        )
        await self._db.commit()
        return cur.rowcount or 0

    async def open_task_for(self, lead_id: str, step_id: str) -> Task | None:
        row = await self._db.fetchone(
            f"""SELECT * FROM tasks WHERE lead_id=? AND step_id=?
                AND status IN ({",".join("?" * len(OPEN_STATUSES))}) LIMIT 1""",
            (lead_id, step_id, *OPEN_STATUSES),
        )
        return _row_to_task(row) if row else None

    async def count_open(self, account: str, action: Action) -> int:
        row = await self._db.fetchone(
            f"""SELECT COUNT(*) AS n FROM tasks WHERE account=? AND action=?
                AND status IN ({",".join("?" * len(OPEN_STATUSES))})""",
            (account, action.value, *OPEN_STATUSES),
        )
        return int(row["n"]) if row else 0

    async def list_by_status(self, status: TaskStatus, limit: int = 50) -> list[Task]:
        rows = await self._db.fetchall(
            "SELECT * FROM tasks WHERE status=? ORDER BY created_at LIMIT ?", (status.value, limit)
        )
        return [_row_to_task(r) for r in rows]

    async def recent(self, limit: int = 10) -> list[Task]:
        rows = await self._db.fetchall(
            "SELECT * FROM tasks WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_task(r) for r in rows]

    async def for_lead(self, lead_id: str) -> list[Task]:
        rows = await self._db.fetchall(
            "SELECT * FROM tasks WHERE lead_id=? ORDER BY created_at", (lead_id,)
        )
        return [_row_to_task(r) for r in rows]

    async def body_sent_recently(self, account: str, body_hash: str, since: datetime) -> bool:
        row = await self._db.fetchone(
            """SELECT 1 FROM tasks WHERE account=? AND body_hash=? AND status='done'
               AND finished_at>=? LIMIT 1""",
            (account, body_hash, iso(since)),
        )
        return row is not None

    async def depth(self, account: str) -> dict[str, int]:
        rows = await self._db.fetchall(
            "SELECT status, COUNT(*) AS n FROM tasks WHERE account=? GROUP BY status", (account,)
        )
        out: dict[str, int] = {s.value: 0 for s in TaskStatus}
        for r in rows:
            out[str(r["status"])] = int(r["n"])
        return out

    async def cancel_open_for_leads(self, lead_ids: list[str], reason: str = "cancelled") -> int:
        if not lead_ids:
            return 0
        marks = ",".join("?" * len(lead_ids))
        cur = await self._db.execute(
            f"""UPDATE tasks SET status='skipped', result=?, finished_at=?
                WHERE status IN ('queued','awaiting_review') AND lead_id IN ({marks})""",
            (dumps({"status": reason}), iso(datetime.now().astimezone()), *lead_ids),
        )
        await self._db.commit()
        return cur.rowcount or 0

    async def raw(self, sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        return await self._db.fetchall(sql, params)
