"""aiosqlite connection wrapper with schema migration and datetime helpers."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA_VERSION = 3

# Incremental changes for databases created at an older version. schema.sql always
# describes the current shape for fresh databases; these bring existing ones up to it.
MIGRATIONS: dict[int, tuple[str, ...]] = {
    2: ("ALTER TABLE leads ADD COLUMN prior_reply_text TEXT",),
    # Which process claimed a running task, so a task left behind by a dead process is
    # requeued at once instead of after a fixed wait, and one a live process is still
    # working on is left alone however long it takes.
    3: ("ALTER TABLE tasks ADD COLUMN claimed_by INTEGER",),
}
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not open")
        return self._conn

    async def open(self) -> Database:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # Several processes share this file (run loop, dashboard, MCP server, one-off
        # commands); wait for a writer rather than fail a weeks-long run on a lock.
        self._conn = await aiosqlite.connect(self.path, timeout=30)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=30000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self.migrate()
        return self

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def migrate(self) -> None:
        await self.conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        cur = await self.conn.execute("SELECT version FROM schema_version")
        row = await cur.fetchone()
        if row is None:
            await self.conn.execute(
                "INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,)
            )
            await self.conn.commit()
            return
        current = int(row[0])
        for version in range(current + 1, SCHEMA_VERSION + 1):
            for statement in MIGRATIONS.get(version, ()):
                try:
                    await self.conn.execute(statement)
                except sqlite3.OperationalError as e:
                    # Already applied: schema.sql created the table in its current shape
                    # (a database that predates the table), or a previous run crashed
                    # between this statement and the version bump. Either way, the
                    # column is there and the migration must not wedge every later start.
                    if "duplicate column" not in str(e).lower():
                        raise
            await self.conn.execute("UPDATE schema_version SET version=?", (version,))
        await self.conn.commit()

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Cursor:
        return await self.conn.execute(sql, params)

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
        cur = await self.conn.execute(sql, params)
        row = await cur.fetchone()
        await cur.close()
        return row

    async def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(sql, params)
        rows = await cur.fetchall()
        await cur.close()
        return list(rows)

    async def commit(self) -> None:
        await self.conn.commit()

    async def __aenter__(self) -> Database:
        return await self.open()

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
