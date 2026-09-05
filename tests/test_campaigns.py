from pathlib import Path

import pytest
from pydantic import ValidationError

from linkedin_agent.campaigns import (
    CampaignError,
    builtin_campaigns,
    find_campaign,
    load_all_user_campaigns,
    load_campaign,
    new_campaign_file,
    resolve_campaign_path,
)
from linkedin_agent.core.messages import campaign_check
from linkedin_agent.models import Campaign


def test_builtin_campaigns_load_and_check_clean():
    paths = builtin_campaigns()
    assert {p.stem for p in paths} == {"default", "inmail", "cold-minimal", "three-week"}
    for p in paths:
        c = load_campaign(p)
        errors, _ = campaign_check(c)
        assert errors == [], (p, errors)


def test_new_campaign_file_and_lookup(settings):
    path = new_campaign_file("mine", settings)
    assert path.exists() and "name: mine" in path.read_text()
    c = find_campaign("mine", settings)
    assert c.name == "mine"
    assert resolve_campaign_path(str(path), settings) == path
    assert load_all_user_campaigns(settings) == {"mine": c}
    with pytest.raises(CampaignError, match="already exists"):
        new_campaign_file("mine", settings)
    with pytest.raises(CampaignError, match="not found"):
        find_campaign("nope", settings)


def test_bad_yaml_reports_clearly(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("name: x\nagent_name: y\nsteps:\n  - {id: a, action: teleport}\n")
    with pytest.raises(CampaignError, match="action"):
        load_campaign(p)
    p.write_text("- not a mapping\n")
    with pytest.raises(CampaignError, match="mapping"):
        load_campaign(p)
    p.write_text("name: [unclosed\n")
    with pytest.raises(CampaignError, match="invalid YAML"):
        load_campaign(p)


# ── campaign-defined windows ───────────────────────────────────────────────


def _with_windows(**windows):
    return {
        "name": "w",
        "agent_name": "Alex",
        "windows": windows,
        "messages": {"m1": "Hi {first_name}."},
        "steps": [
            {"id": "warm.visit", "action": "visit", "after": "0d", "window": "any"},
            {
                "id": "post.m1",
                "action": "message",
                "after": "0d",
                "window": "gulf",
                "params": {"template": "m1"},
            },
        ],
    }


GULF = {"days": ["sun", "mon", "tue", "wed", "thu"], "hours": ["09:00-12:00", "16:00-18:00"]}


def test_a_campaign_can_define_its_own_window():
    from linkedin_agent.core.timezone import describe_window

    c = Campaign(**_with_windows(gulf=GULF))
    assert describe_window(c.window_specs["gulf"]) == "Sun–Thu 09:00-12:00, 16:00-18:00"


def test_a_step_may_not_name_a_window_that_does_not_exist():
    body = _with_windows(gulf=GULF)
    body["steps"][1]["window"] = "nonsense"
    with pytest.raises(ValidationError, match="unknown window 'nonsense'"):
        Campaign(**body)


def test_a_campaign_with_no_windows_block_still_uses_the_built_ins():
    body = _with_windows()
    body["steps"][1]["window"] = "send"
    c = Campaign(**body)
    assert c.window_specs == {}


def test_bad_days_and_hours_are_rejected_at_load():
    for windows in (
        {"gulf": {"days": ["funday"], "hours": ["09:00-12:00"]}},
        {"gulf": {"days": ["mon"], "hours": ["18:00-09:00"]}},
        {"gulf": {"days": ["mon"], "hours": ["9-12"]}},
        {"gulf": {"days": [], "hours": ["09:00-12:00"]}},
    ):
        with pytest.raises(ValidationError):
            Campaign(**_with_windows(**windows))


def test_check_warns_about_unused_redefined_and_tiny_windows():
    c = Campaign(**_with_windows(gulf=GULF, evening={"days": ["tue"], "hours": ["18:00-21:00"]}))
    _, warnings = campaign_check(c)
    assert any("windows.evening: defined but no step uses it" in w for w in warnings)

    body = _with_windows(gulf=GULF, send={"days": ["fri"], "hours": ["10:00-18:00"]})
    _, warnings = campaign_check(Campaign(**body))
    assert any("redefines the built-in send window" in w for w in warnings)

    body = _with_windows(gulf={"days": ["mon"], "hours": ["09:00-10:00"]})
    _, warnings = campaign_check(Campaign(**body))
    assert any("only 60 minutes a week" in w for w in warnings)


# ── the three-week template ────────────────────────────────────────────────


def _three_week():
    return load_campaign(next(p for p in builtin_campaigns() if p.stem == "three-week"))


def _route(campaign, step_id, status, *, branch="posts", entered=None, now=None):
    from datetime import datetime, timedelta, timezone

    from linkedin_agent.core.sequence import route
    from linkedin_agent.models import TaskResult

    step = next(s for s in campaign.steps if s.id == step_id)
    now = now or datetime(2026, 1, 1, tzinfo=timezone.utc)
    entered = entered if entered is not None else now - timedelta(days=99)
    return route(campaign, step, step.action, TaskResult(status=status), branch, entered, now)


def test_three_week_finishes_every_path_inside_the_budget():
    """Warm-up 4 days, invite, 8 days for acceptance, three follow-ups 3 days apart:
    the longest accepted path has to stay under 21 days."""
    from linkedin_agent.models import parse_duration

    c = _three_week()
    by_id = {s.id: s for s in c.steps}
    warm = sum(
        parse_duration(by_id[i].after).days
        for i in ("warm.visit", "warm.follow", "warm.like", "warm.comment", "invite.posts")
    )
    assert warm == 4
    assert by_id["wait.accept"].params["until_days"] == 8
    assert by_id["wait.accept"].params["repeat_every"] == "2d"
    replies = sum(parse_duration(by_id[i].after).days for i in ("post.r1", "post.r2", "post.r3"))
    assert warm + 8 + replies <= 21


def test_three_week_defines_its_own_invite_and_followup_windows():
    from linkedin_agent.core.timezone import describe_window

    c = _three_week()
    specs = c.window_specs
    assert describe_window(specs["invite"]) == "Tue–Fri 08:30-11:00, 14:00-16:00"
    assert describe_window(specs["followup"]) == "Mon–Fri 08:30-11:00, 14:00-16:00"
    # the invite keeps the good hours; only the day range is widened
    for step in c.steps:
        if step.action.value == "connect":
            assert step.window == "invite"
        if step.action.value in ("message", "inmail"):
            assert step.window == "followup"


def test_three_week_sends_one_inmail_when_the_invite_is_never_accepted():
    c = _three_week()
    assert _route(c, "wait.accept", "pending") == "withdraw", "timeout beats ordinary routing"
    assert _route(c, "withdraw", "withdrawn") == "fallback.inmail"
    assert _route(c, "withdraw", "not_pending") == "fallback.inmail"
    assert _route(c, "fallback.inmail", "sent") == "fallback.reply"
    assert _route(c, "fallback.reply", "replied") == "end:replied"
    assert _route(c, "fallback.reply", "none") == "end:nurture"


def test_three_week_ends_cleanly_when_there_are_no_inmail_credits():
    """Without Sales Navigator the step reports cannot_message, which must land the lead
    where it would have landed without the fallback at all — not in cannot_contact."""
    c = _three_week()
    assert _route(c, "fallback.inmail", "cannot_message") == "end:not_accepted"


def test_three_week_accepted_path_runs_the_three_messages():
    c = _three_week()
    from datetime import datetime, timezone

    just_now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert _route(c, "wait.accept", "connected", entered=just_now) == "post.m1"
    assert _route(c, "post.r1", "none") == "post.m2"
    assert _route(c, "post.r2", "none") == "post.m3"
    assert _route(c, "post.r3", "none") == "end:nurture"
    for r in ("post.r1", "post.r2", "post.r3"):
        assert _route(c, r, "replied") == "end:replied"
