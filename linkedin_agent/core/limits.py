"""Daily/weekly caps, account ramp, per-prospect spacing and the acceptance-rate governor.

All functions are pure; the scheduler and runner feed them counts from the action log.
"""

from __future__ import annotations

import math
from datetime import datetime

from ..models import Action, GovernorState

# (per day, per rolling 7 days or None)
BASE_CAPS: dict[Action, tuple[int, int | None]] = {
    Action.CONNECT: (20, 90),
    Action.MESSAGE: (40, None),
    Action.INMAIL: (20, None),
    Action.VISIT: (60, None),
    Action.LIKE_POST: (30, None),
    Action.COMMENT_POST: (8, None),
    Action.FOLLOW: (15, None),
    Action.WITHDRAW_INVITE: (15, None),
    Action.CHECK_CONNECTION: (60, None),
    Action.CHECK_REPLIES: (60, None),
}

# Ceilings copied from the cloud rate limiter so local caps never exceed the product's.
TIER_CEILINGS: dict[str, dict[Action, int]] = {
    "free": {Action.VISIT: 10, Action.CONNECT: 4, Action.MESSAGE: 2, Action.INMAIL: 2},
    "pro": {Action.VISIT: 60, Action.CONNECT: 20, Action.MESSAGE: 30, Action.INMAIL: 30},
    "ultimate": {Action.VISIT: 60, Action.CONNECT: 20, Action.MESSAGE: 30, Action.INMAIL: 30},
}

# (account age in days is strictly less than, multiplier)
RAMP: list[tuple[int, float]] = [(7, 0.25), (14, 0.40), (21, 0.60), (28, 0.80)]

GOVERNED_ACTIONS: frozenset[Action] = frozenset({Action.CONNECT, Action.INMAIL})

# Governor thresholds on the rolling acceptance rate.
GOVERNOR_PAUSE_BELOW = 0.20
GOVERNOR_HALVE_BELOW = 0.30
GOVERNOR_RECOVER_AT = 0.35
GOVERNOR_MIN_SAMPLE = 10


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def account_age_days(first_action_at: datetime | None, now: datetime) -> int:
    if first_action_at is None:
        return 0
    return max(0, (now - first_action_at).days)


def ramp_multiplier(age_days: int) -> float:
    for lt, mult in RAMP:
        if age_days < lt:
            return mult
    return 1.0


def ramp_week(age_days: int) -> int:
    return age_days // 7 + 1


def effective_cap(
    action: Action,
    age_days: int,
    governor: GovernorState = GovernorState.NORMAL,
    user_cap: int | None = None,
    tier: str = "pro",
) -> tuple[int, int | None]:
    """Return (daily cap, weekly cap or None) after ramp, governor, user cap and tier ceiling."""
    day, week = BASE_CAPS[action]
    m = ramp_multiplier(age_days)
    if action in GOVERNED_ACTIONS:
        if governor == GovernorState.PAUSED:
            return 0, 0
        if governor == GovernorState.HALVED:
            m *= 0.5
    day = max(1, _round_half_up(day * m))
    ceiling = TIER_CEILINGS.get(tier, TIER_CEILINGS["pro"]).get(action)
    if ceiling is not None:
        day = min(day, ceiling)
    if user_cap is not None and user_cap > 0:
        day = min(day, user_cap)
    week_cap = None if week is None else max(day, _round_half_up(week * m))
    return day, week_cap


def remaining(day_count: int, week_count: int, caps: tuple[int, int | None]) -> int:
    day_cap, week_cap = caps
    left = day_cap - day_count
    if week_cap is not None:
        left = min(left, week_cap - week_count)
    return max(0, left)


def spacing_ok(touches_24h: int, touches_48h: int) -> bool:
    """At most one touch per prospect per day and two in any 48 hours."""
    return touches_24h < 1 and touches_48h < 2


def governor_state(
    current: GovernorState, acceptance_rate: float | None, sample: int
) -> GovernorState:
    """Hysteresis: tighten quickly, loosen only once the rate is clearly healthy."""
    if acceptance_rate is None or sample < GOVERNOR_MIN_SAMPLE:
        return current
    if acceptance_rate < GOVERNOR_PAUSE_BELOW:
        return GovernorState.PAUSED
    if acceptance_rate < GOVERNOR_HALVE_BELOW:
        return GovernorState.HALVED
    if acceptance_rate >= GOVERNOR_RECOVER_AT:
        return GovernorState.NORMAL
    return current
