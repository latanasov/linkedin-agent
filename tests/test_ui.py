"""The local web UI: every endpoint against the fake-backed deps, via ASGI (no sockets)."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from linkedin_agent.bootstrap import App
from linkedin_agent.core import sequence as seqeng
from linkedin_agent.core.runner import process_task
from linkedin_agent.models import LeadStage, TaskStatus
from linkedin_agent.scheduler import tick
from linkedin_agent.ui import STATIC_DIR, create_ui_app
from tests.conftest import NOW, make_lead


@pytest.fixture
async def client(deps, db, settings, clock):
    app = App(settings=settings, db=db, deps=deps)
    ui = create_ui_app(app, now=clock)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ui), base_url="http://t") as c:
        yield c


async def seed_lead(deps, step="warm.visit", branch=None, **overrides):
    lead = make_lead(**overrides)
    await deps.leads.upsert_many([lead])
    s = seqeng.new_sequence(lead, deps.campaigns["test"], NOW)
    s.step_id, s.branch = step, branch
    await deps.leads.save_sequence(s)
    return lead


async def test_index_serves_the_page(client):
    r = await client.get("/")
    assert r.status_code == 200 and "<title>linkedin-agent</title>" in r.text
    assert STATIC_DIR.joinpath("index.html").exists()


async def test_overview_reports_account_usage_queue_and_stages(client, deps, executor):
    lead = await seed_lead(deps, posts=[])
    await tick(deps, "default", NOW)
    t = await deps.queue.claim_next("default", NOW)
    await process_task(t, deps)
    o = (await client.get("/api/overview")).json()
    assert o["account"]["login"] == "not_logged_in" and o["account"]["ramp_week"] == 1
    assert o["account"]["breaker_tripped"] is False and o["account"]["governor"] == "normal"
    visit = next(u for u in o["usage"] if u["action"] == "visit")
    assert visit["day"] == 1 and visit["day_cap"] >= 1
    assert o["stages"] == {"warming": 1}
    assert o["queue"]["done"] == 1 and o["queue"]["inbox"] == 0
    assert o["recent"][0]["lead_name"] == "Jane Doe" and o["recent"][0]["result_status"] == "ok"
    assert o["campaigns"] == ["test"] and o["fast_test"] is False
    assert lead.id == o["recent"][0]["lead_id"]


async def test_leads_list_and_detail(client, deps):
    lead = await seed_lead(
        deps, step="wait.accept", branch="posts", invited_at=NOW - timedelta(days=2)
    )
    rows = (await client.get("/api/leads")).json()
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "Jane Doe" and row["stage"] == "new" and row["step_id"] == "wait.accept"
    assert row["branch"] == "posts" and row["stalled"] is False and row["posts"] == 2
    assert (await client.get("/api/leads?campaign=other")).json() == []

    detail = (await client.get(f"/api/leads/{lead.slug}")).json()
    assert detail["id"] == lead.id and detail["profile"]["headline"] == "VP Engineering at Acme"
    assert detail["post_list"][0]["text"] and detail["history"] == [] and detail["tasks"] == []
    r = await client.get("/api/leads/nobody")
    assert r.status_code == 404 and "no lead matches" in r.json()["error"]


async def test_lead_actions_retry_skip_restart_handled(client, deps):
    lead = await seed_lead(deps, step="warm.follow", branch="posts")
    seq = await deps.leads.get_sequence(lead.id)
    seq.next_due_at = None  # stalled
    await deps.leads.save_sequence(seq)
    assert (await client.get("/api/leads")).json()[0]["stalled"] is True

    r = await client.post(f"/api/leads/{lead.id}/retry")
    assert r.json()["message"] == "re-armed step warm.follow"
    assert (await deps.leads.get_sequence(lead.id)).next_due_at == NOW

    r = await client.post(f"/api/leads/{lead.id}/skip")
    assert r.json()["message"].startswith("skipped warm.follow")

    r = await client.post(f"/api/leads/{lead.id}/restart", json={"step": "post.m1"})
    assert r.json()["message"].startswith("restarted at post.m1 (stage connected")
    r = await client.post(f"/api/leads/{lead.id}/restart")
    assert r.json()["message"].startswith("restarted at warm.visit (stage new")

    lead.stage = LeadStage.REPLIED
    lead.replied_at = NOW
    await deps.leads.update(lead)
    inbox = (await client.get("/api/inbox")).json()
    assert [x["id"] for x in inbox] == [lead.id]
    r = await client.post(f"/api/leads/{lead.id}/handled")
    assert "marked done" in r.json()["message"]
    assert (await deps.leads.get(lead.id)).stage == LeadStage.DONE
    assert (await client.get("/api/inbox")).json() == []
    assert (await client.post("/api/leads/nobody/retry")).status_code == 404


async def test_tasks_and_log_endpoints(client, deps, executor):
    await seed_lead(deps, posts=[])
    await tick(deps, "default", NOW)
    queued = (await client.get("/api/tasks?status=queued")).json()
    assert len(queued) == 1 and queued[0]["action"] == "visit" and queued[0]["status"] == "queued"
    t = await deps.queue.claim_next("default", NOW)
    await process_task(t, deps)
    assert (await client.get("/api/tasks?status=queued")).json() == []
    recent = (await client.get("/api/tasks")).json()
    assert recent[0]["status"] == "done" and recent[0]["result_status"] == "ok"
    assert (await client.get("/api/tasks?status=bogus")).status_code == 400
    log = (await client.get("/api/log")).json()
    assert len(log) == 1 and log[0]["action"] == "visit" and log[0]["ok"] in (1, True)


async def test_review_endpoints_approve_and_reject(client, deps, campaign):
    campaign.review_comments = True
    lead = await seed_lead(deps, step="warm.comment", branch="posts")
    rep = await tick(deps, "default", NOW)
    assert rep.reviews == 1
    items = (await client.get("/api/review")).json()
    assert len(items) == 1 and items[0]["context"]["lead"] == "Jane Doe" and items[0]["draft"]
    task_id = items[0]["task_id"]

    r = await client.post(
        f"/api/review/{task_id}",
        json={"approve": True, "text": "Removing the approval step is the part most teams skip."},
    )
    assert r.json()["message"] == "approved; queued"
    task = await deps.queue.get(task_id)
    assert task.status == TaskStatus.QUEUED and task.params["text"].startswith("Removing")
    assert (await client.get("/api/review")).json() == []

    # a second draft, rejected
    seq = await deps.leads.get_sequence(lead.id)
    seq.step_id, seq.next_due_at = "warm.comment", NOW
    await deps.leads.save_sequence(seq)
    await deps.queue.finish(task_id, None, TaskStatus.SKIPPED)
    await tick(deps, "default", NOW)
    items = (await client.get("/api/review")).json()
    r = await client.post(f"/api/review/{items[0]['task_id']}", json={"approve": False})
    assert "rejected" in r.json()["message"]
    assert (await client.post("/api/review/nope", json={"approve": True})).status_code == 404


async def test_report_endpoint(client, deps):
    await seed_lead(
        deps,
        invited_at=NOW - timedelta(days=5),
        connected_at=NOW - timedelta(days=3),
        stage=LeadStage.CONNECTED,
    )
    r = (await client.get("/api/report?since=14d")).json()
    assert r["leads"] == 1 and r["invited"] == 1 and r["accepted"] == 1
    assert r["acceptance_rate"] == 1.0 and r["median_days_to_accept"] == 2.0
    assert r["reply_rate"] is None and r["stages"] == {"connected": 1}
    assert r["rows"][0]["name"] == "Jane Doe"
    assert (await client.get("/api/report?since=nonsense")).status_code == 400


async def test_campaign_endpoints_pause_resume(client, deps):
    lead = await seed_lead(deps)
    await tick(deps, "default", NOW)
    camps = (await client.get("/api/campaigns")).json()
    assert camps[0]["name"] == "test" and camps[0]["paused"] is False
    assert camps[0]["steps"][0]["id"] == "warm.visit" and camps[0]["leads"] == {"new": 1}

    r = await client.post("/api/campaigns/test/pause")
    assert r.json()["message"] == "Paused 1 sequence(s), cancelled 1 queued task(s)."
    assert (await client.get("/api/campaigns")).json()[0]["paused"] is True
    r = await client.post("/api/campaigns/test/resume")
    assert r.json()["message"] == "Resumed 1 sequence(s)."
    assert (await client.post("/api/campaigns/ghost/pause")).status_code == 404
    assert lead.id  # unchanged lead survives


async def test_breaker_reset(client, deps):
    acct = await deps.accounts.get("default")
    acct.tripped_until, acct.trip_reason = NOW + timedelta(hours=48), "test"
    await deps.accounts.save(acct)
    assert (await client.get("/api/overview")).json()["account"]["breaker_tripped"] is True
    r = await client.post("/api/breaker/reset")
    assert r.json()["message"] == "Circuit breaker reset."
    assert (await client.get("/api/overview")).json()["account"]["breaker_tripped"] is False


def test_ui_command_is_registered():
    from typer.testing import CliRunner

    from linkedin_agent import cli

    r = CliRunner().invoke(cli.app, ["ui", "--help"])
    assert r.exit_code == 0 and "dashboard" in r.output.lower()
