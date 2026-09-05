from datetime import timedelta

import pytest

from linkedin_agent.core.limits import (
    account_age_days,
    effective_cap,
    governor_state,
    ramp_multiplier,
    ramp_week,
    remaining,
    spacing_ok,
)
from linkedin_agent.models import Action, GovernorState
from tests.conftest import NOW


def test_ramp_table():
    assert ramp_multiplier(0) == 0.25
    assert ramp_multiplier(6) == 0.25
    assert ramp_multiplier(7) == 0.40
    assert ramp_multiplier(20) == 0.60
    assert ramp_multiplier(27) == 0.80
    assert ramp_multiplier(28) == 1.0
    assert ramp_week(0) == 1 and ramp_week(13) == 2 and ramp_week(28) == 5


def test_account_age():
    assert account_age_days(None, NOW) == 0
    assert account_age_days(NOW - timedelta(days=10, hours=5), NOW) == 10


def test_effective_cap_ramp_and_tier():
    assert effective_cap(Action.CONNECT, 0) == (5, 23)  # 20*.25, 90*.25 rounded
    assert effective_cap(Action.CONNECT, 30) == (20, 90)
    # tier ceiling applies after ramp
    assert effective_cap(Action.CONNECT, 30, tier="free") == (4, 90)
    assert effective_cap(Action.MESSAGE, 30, tier="free") == (2, None)
    # user cap can lower, never raise
    assert effective_cap(Action.CONNECT, 30, user_cap=10)[0] == 10
    assert effective_cap(Action.CONNECT, 30, user_cap=500)[0] == 20
    assert effective_cap(Action.CONNECT, 30, user_cap=0)[0] == 20


def test_effective_cap_governor_only_touches_invites():
    assert effective_cap(Action.CONNECT, 30, GovernorState.HALVED) == (10, 45)
    assert effective_cap(Action.CONNECT, 30, GovernorState.PAUSED) == (0, 0)
    assert effective_cap(Action.INMAIL, 30, GovernorState.PAUSED) == (0, 0)
    assert effective_cap(Action.MESSAGE, 30, GovernorState.PAUSED) == (30, None)  # pro ceiling
    assert effective_cap(Action.VISIT, 30, GovernorState.HALVED) == (60, None)


def test_effective_cap_never_below_one_when_not_paused():
    assert effective_cap(Action.COMMENT_POST, 0)[0] >= 1  # 8*.25 = 2
    assert effective_cap(Action.INMAIL, 0, GovernorState.HALVED)[0] >= 1


def test_remaining_uses_both_windows():
    assert remaining(3, 10, (20, 90)) == 17
    assert remaining(3, 88, (20, 90)) == 2
    assert remaining(25, 0, (20, 90)) == 0
    assert remaining(3, 10, (20, None)) == 17


@pytest.mark.parametrize("t24,t48,ok", [(0, 0, True), (0, 1, True), (1, 1, False), (0, 2, False)])
def test_spacing(t24, t48, ok):
    assert spacing_ok(t24, t48) is ok


def test_governor_hysteresis():
    N, H, P = GovernorState.NORMAL, GovernorState.HALVED, GovernorState.PAUSED
    assert governor_state(N, None, 0) == N
    assert governor_state(N, 0.1, 5) == N  # too small a sample
    assert governor_state(N, 0.25, 20) == H
    assert governor_state(N, 0.15, 20) == P
    assert governor_state(H, 0.32, 20) == H  # not yet recovered
    assert governor_state(H, 0.36, 20) == N
    assert governor_state(P, 0.25, 20) == H
