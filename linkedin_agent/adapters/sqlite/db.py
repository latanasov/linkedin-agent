"""aiosqlite connection wrapper with schema migration and datetime helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA_VERSION = 2

# Incremental changes for databases created at an older version. schema.sql always
# describes the current shape for fresh databases; these bring existing ones up to it.
MIGRATIONS: dict[int, tuple[str, ...]] = {
    2: ("ALTER TABLE leads ADD COLUMN prior_reply_text TEXT",),
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
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
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
                await self.conn.execute(statement)
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
