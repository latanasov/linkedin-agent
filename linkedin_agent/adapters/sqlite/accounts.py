from __future__ import annotations

from ...models import AccountState, GovernorState
from .db import Database, iso, parse_dt


class SqliteAccountStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, name: str) -> AccountState:
        row = await self._db.fetchone("SELECT * FROM accounts WHERE name=?", (name,))
        if row is None:
            await self._db.execute("INSERT INTO accounts(name) VALUES (?)", (name,))
            await self._db.commit()
            return AccountState(name=name)
        return AccountState(
            name=row["name"],
            first_action_at=parse_dt(row["first_action_at"]),
            logged_in_at=parse_dt(row["logged_in_at"]),
            user_agent=row["user_agent"],
            tripped_until=parse_dt(row["tripped_until"]),
            trip_reason=row["trip_reason"],
            consecutive_failures=int(row["consecutive_failures"] or 0),
            session_expired_at=parse_dt(row["session_expired_at"]),
            governor_state=GovernorState(row["governor_state"] or "normal"),
            governor_checked_at=parse_dt(row["governor_checked_at"]),
        )

    async def save(self, state: AccountState) -> None:
        await self._db.execute(
            """INSERT INTO accounts(name, first_action_at, logged_in_at, user_agent, tripped_until, trip_reason,
                                    consecutive_failures, session_expired_at, governor_state, governor_checked_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET first_action_at=excluded.first_action_at,
                 logged_in_at=excluded.logged_in_at, user_agent=excluded.user_agent,
                 tripped_until=excluded.tripped_until, trip_reason=excluded.trip_reason,
                 consecutive_failures=excluded.consecutive_failures,
                 session_expired_at=excluded.session_expired_at, governor_state=excluded.governor_state,
                 governor_checked_at=excluded.governor_checked_at""",
            (
                state.name,
                iso(state.first_action_at),
                iso(state.logged_in_at),
                state.user_agent,
                iso(state.tripped_until),
                state.trip_reason,
                state.consecutive_failures,
                iso(state.session_expired_at),
                state.governor_state.value,
                iso(state.governor_checked_at),
            ),
        )
        await self._db.commit()
