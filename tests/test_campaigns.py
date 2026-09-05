from pathlib import Path

import pytest

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
