from datetime import timedelta

import pytest
from pydantic import ValidationError

from linkedin_agent.models import Campaign, LeadRecord, TaskResult, parse_duration
from tests.conftest import make_campaign


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2d", timedelta(days=2)),
        ("6h", timedelta(hours=6)),
        ("30m", timedelta(minutes=30)),
        ("45s", timedelta(seconds=45)),
        ("1w", timedelta(weeks=1)),
        (90, timedelta(seconds=90)),
    ],
)
def test_parse_duration(value, expected):
    assert parse_duration(value) == expected


@pytest.mark.parametrize("bad", ["2 days", "d", "", "1y"])
def test_parse_duration_rejects(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)


def test_lead_slug_and_display_name():
    lead = LeadRecord(campaign="c", linkedin_url="https://www.linkedin.com/in/jane-doe-123/")
    assert lead.slug == "jane-doe-123"
    assert lead.display_name == "jane-doe-123"
    lead.first_name = "Jane"
    assert lead.display_name == "Jane"
    nav = LeadRecord(
        campaign="c", linkedin_url="https://www.linkedin.com/sales/lead/ACwAAA,NAME,abc"
    )
    assert nav.slug == "ACwAAA"


def test_campaign_rejects_duplicate_step_ids():
    data = make_campaign().model_dump()
    data["steps"].append(dict(data["steps"][0]))
    with pytest.raises(ValidationError, match="Duplicate"):
        Campaign.model_validate(data)


def test_campaign_rejects_unknown_route_target():
    data = make_campaign().model_dump()
    data["steps"][0]["on_result"] = {"ok": "nowhere"}
    with pytest.raises(ValidationError, match="unknown step"):
        Campaign.model_validate(data)


def test_campaign_rejects_unknown_end_stage():
    data = make_campaign().model_dump()
    data["steps"][0]["on_result"] = {"ok": "end:mars"}
    with pytest.raises(ValidationError, match="unknown end stage"):
        Campaign.model_validate(data)


def test_campaign_rejects_missing_template():
    data = make_campaign().model_dump()
    data["steps"][-2]["params"] = {"template": "m9"}
    with pytest.raises(ValidationError, match="not defined in messages"):
        Campaign.model_validate(data)


def test_campaign_rejects_bad_delay():
    data = make_campaign().model_dump()
    data["steps"][0]["after"] = "soon"
    with pytest.raises(ValidationError):
        Campaign.model_validate(data)


def test_task_result_from_raw_variants():
    assert TaskResult.from_raw({"status": "sent", "error": None}).status == "sent"
    r = TaskResult.from_raw({"error": "login_required"})
    assert r.status == "failed" and r.error == "login_required"
    assert TaskResult.from_raw({"full_name": "Jane"}).status == "ok"
    assert TaskResult.from_raw({"full_name": "Jane"}).data == {"full_name": "Jane"}
    assert TaskResult.from_raw(None).status == "failed"
    assert TaskResult.from_raw("garbage").error.startswith("unparseable")
