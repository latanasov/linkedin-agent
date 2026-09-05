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
    assert {p.stem for p in paths} == {"default", "inmail", "cold-minimal"}
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
