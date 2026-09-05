"""Service: the operations behind the MCP tools, against fake-backed deps."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

from linkedin_agent.adapters.csv_import import parse_rows
from linkedin_agent.core import sequence as seqeng
from linkedin_agent.core.runner import process_task
from linkedin_agent.models import Action, LeadStage, TaskStatus
from linkedin_agent.scheduler import tick
from linkedin_agent.service import (
    Service,
    ServiceError,
    clear_heartbeat,
    format_import,
    run_state,
    write_heartbeat,
)
from tests.conftest import NOW, make_lead


@pytest.fixture
def svc(deps, settings) -> Service:
    return Service(deps, settings)


async def seed(deps, step="warm.visit", branch=None, **overrides):
    lead = make_lead(**overrides)
    await deps.leads.upsert_many([lead])
    s = seqeng.new_sequence(lead, deps.campaigns["test"], NOW)
    s.step_id, s.branch = step, branch
    await deps.leads.save_sequence(s)
    return lead


def test_parse_rows_accepts_api_shaped_input():
    r = parse_rows(
        [
            {
                "linkedin_url": "https://www.linkedin.com/in/janedoe",
                "First_Name": "Jane",
                "pain_point": "costs",
            },
            {"url": "linkedin.com/in/bob/", "name": "Bob Smith", "company": "Contoso"},
            {"linkedin_url": "https://evil.com/x"},
            {"linkedin_url": "https://www.linkedin.com/in/janedoe/"},
        ],
        "test",
        "UTC",
    )
    assert [ld.first_name for ld in r.leads] == ["Jane", "Bob"]
    assert (
        r.leads[1].last_name == "Smith"
        and r.leads[1].linkedin_url == "https://linkedin.com/in/bob/"
    )
    assert r.leads[0].custom_fields == {"pain_point": "costs"} and r.custom_columns == {
        "pain_point"
    }
    assert [why for _, why in r.skipped] == [
        "invalid LinkedIn URL: 'https://evil.com/x'",
        "duplicate URL in file",
    ]


async def test_import_rows_starts_sequences_and_reports(svc, deps):
    summary = await svc.import_rows(
        [
            {"linkedin_url": "https://www.linkedin.com/in/janedoe", "first_name": "Jane"},
            {"linkedin_url": "nope"},
        ],
        "test",
    )
    assert (summary.imported, summary.new, summary.updated, summary.sequences_started) == (
        1,
        1,
        0,
        1,
    )
    assert summary.skipped == [(3, "invalid LinkedIn URL: 'nope'")]
    assert "Imported 1 leads (1 new" in format_import(summary) and "row 3" in format_import(summary)
    # re-import updates, does not restart
    again = await svc.import_rows(
        [{"linkedin_url": "https://www.linkedin.com/in/janedoe", "company": "Acme"}], "test"
    )
    assert (again.new, again.updated, again.sequences_started) == (0, 1, 0)
    with pytest.raises(ServiceError, match="not found"):
        await svc.import_rows([], "ghost")


async def test_import_csv_from_disk(svc, tmp_path):
    f = tmp_path / "l.csv"
    f.write_text(
        "linkedin_url,first_name,custom_x\nhttps://www.linkedin.com/in/a1,Al,1\n", encoding="utf-8"
    )
    summary = await svc.import_csv(str(f), "test")
    assert summary.imported == 1
    with pytest.raises(ServiceError, match="does not exist"):
        await svc.import_csv(str(tmp_path / "missing.csv"), "test")


async def test_status_leads_lead_and_preview(svc, deps, executor):
    lead = await seed(deps, posts=[])
    await tick(deps, "default", NOW)
    await process_task(await deps.queue.claim_next("default", NOW), deps)
    st = await svc.status()
    assert st["run"]["active"] is False and st["leads_by_stage"] == {"warming": 1}
    assert st["account"]["ramp_week"] == 1 and st["recent"][0]["result_status"] == "ok"
    rows = await svc.leads()
    assert rows[0]["name"] == "Jane Doe" and rows[0]["step_id"] == "warm.follow"
    assert (
        await svc.leads(stage="new") == [] and (await svc.leads(search="acme"))[0]["id"] == lead.id
    )
    detail = await svc.lead("janedoe")
    assert (
        detail["history"][0]["step_id"] == "warm.visit" and detail["tasks"][0]["action"] == "visit"
    )
    pv = await svc.preview(lead.linkedin_url)
    assert set(pv["messages"]) == set(deps.campaigns["test"].messages)
    assert pv["messages"]["m1"]["text"].startswith("Hi Jane")
    with pytest.raises(ServiceError, match="no lead"):
        await svc.lead("nobody")


async def test_lead_controls_and_inbox(svc, deps):
    lead = await seed(deps, step="warm.follow", branch="posts")
    assert (await svc.retry("janedoe")) == "re-armed step warm.follow"
    assert (await svc.skip("janedoe")).startswith("skipped warm.follow")
    assert (await svc.restart("janedoe", "post.m1")).startswith("restarted at post.m1")
    lead.stage, lead.replied_at = LeadStage.REPLIED, NOW
    await deps.leads.update(lead)
    assert [r["id"] for r in await svc.inbox()] == [lead.id]
    assert "marked done" in await svc.mark_handled("janedoe")
    assert await svc.inbox() == []


async def test_campaign_read_check_write_new(svc, deps, settings):
    assert "default" in svc.campaign_templates()
    got = svc.campaign_get("default")
    assert (
        got["name"] == "default" and got["errors"] == [] and got["steps"][0]["id"] == "warm.visit"
    )
    bad = svc.campaign_check_text(
        "name: x\nagent_name: A\nsteps:\n  - {id: a, action: visit, on_result: {ok: zzz}}\n"
    )
    assert bad["ok"] is False and "unknown step" in bad["errors"][0]
    with pytest.raises(ServiceError, match="not written"):
        svc.campaign_write("x", "name: x\nsteps: []\n")
    with pytest.raises(ServiceError, match="expected 'y'"):
        svc.campaign_write("y", "name: x\nagent_name: A\nsteps:\n  - {id: a, action: visit}\n")
    ok = svc.campaign_write(
        "x",
        "name: x\nagent_name: A\nmessages: {m1: 'Hi {first_name}'}\nsteps:\n  - {id: a, action: visit}\n",
    )
    assert ok["ok"] and (settings.campaigns_dir / "x.yaml").exists() and "x" in deps.campaigns
    created = svc.campaign_new("mine", "inmail")
    assert created["name"] == "mine" and any(s["action"] == "inmail" for s in created["steps"])
    with pytest.raises(ServiceError, match="already exists"):
        svc.campaign_new("mine")
    names = [c["name"] for c in await svc.campaigns()]
    assert names == ["mine", "test", "x"]  # "test" is registered in memory by the fixture


async def test_pause_resume_review_breaker(svc, deps, campaign):
    await seed(deps)
    assert (await svc.pause("test")).startswith("Paused 1")
    assert (await svc.resume("test")).startswith("Resumed 1")
    with pytest.raises(ServiceError):
        await svc.pause("ghost")
    campaign.review_comments = True
    lead = await seed(
        deps, step="warm.comment", branch="posts", linkedin_url="https://www.linkedin.com/in/rev/"
    )
    await tick(deps, "default", NOW)
    items = await svc.review_pending()
    assert len(items) == 1 and items[0]["context"]["lead"] == "Jane Doe"
    assert await svc.review_decide(items[0]["task_id"], True, None) == "approved; queued"
    with pytest.raises(ServiceError, match="no review item"):
        await svc.review_decide("nope", False, None)
    acct = await deps.accounts.get("default")
    acct.tripped_until = NOW + timedelta(hours=1)
    await deps.accounts.save(acct)
    assert await svc.breaker_reset() == "Circuit breaker reset."
    assert (await deps.accounts.get("default")).tripped_until is None
    assert lead.id


async def test_enqueue_action_queues_for_the_run_loop(svc, deps, executor):
    out = await svc.enqueue_action("visit", "https://www.linkedin.com/in/janedoe")
    assert (
        out["queued"] and out["run_active"] is False and "start `linkedin-agent run`" in out["note"]
    )
    t = await deps.queue.get(out["task_id"])
    assert (
        t.action == Action.VISIT
        and t.status == TaskStatus.QUEUED
        and t.params["lead_name"] == "https://www.linkedin.com/in/janedoe"
    )
    # the run loop executes it and the result is readable
    claimed = await deps.queue.claim_next("default", NOW)
    await process_task(claimed, deps)
    assert (await svc.task(out["task_id"]))["result_status"] == "ok"
    msg_task = await svc.enqueue_action(
        "message", "https://www.linkedin.com/in/janedoe", {"text": "Hi"}
    )
    assert (await deps.queue.get(msg_task["task_id"])).params["text"] == "Hi"
    for action, url, params, err in (
        ("dance", "https://www.linkedin.com/in/x", None, "unknown action"),
        ("visit", "https://evil.com/x", None, "LinkedIn"),
        ("message", "https://www.linkedin.com/in/x", None, "needs params.text"),
        ("inmail", "https://www.linkedin.com/in/x", {"text": "t"}, "needs params.subject"),
        ("comment", "https://www.linkedin.com/in/x", None, "needs params.text"),
    ):
        with pytest.raises(ServiceError, match=err):
            await svc.enqueue_action(action, url, params)
    with pytest.raises(ServiceError, match="no task"):
        await svc.task("nope")


def test_run_state_reads_the_heartbeat(settings, clock):
    assert run_state(settings)["active"] is False
    write_heartbeat(settings, "default", clock.now)
    st = run_state(settings)
    assert st["active"] is True and st["pid"] == os.getpid() and st["account"] == "default"
    old = run_state(settings, clock.now + timedelta(minutes=10))
    assert old["active"] is False and "last heartbeat" in old["reason"]
    clear_heartbeat(settings)
    clear_heartbeat(settings)  # idempotent
    assert run_state(settings)["active"] is False


async def test_report_tasks_and_log(svc, deps, executor):
    await seed(deps, posts=[])
    await tick(deps, "default", NOW)
    await process_task(await deps.queue.claim_next("default", NOW), deps)
    r = await svc.report(None, "7d")
    assert r["leads"] == 1 and "rows" not in r
    with pytest.raises(ServiceError):
        await svc.report(None, "soon")
    assert (await svc.tasks("done"))[0]["action"] == "visit" and await svc.tasks("queued") == []
    with pytest.raises(ServiceError, match="unknown task status"):
        await svc.tasks("bogus")
    assert (await svc.log())[0]["action"] == "visit"
