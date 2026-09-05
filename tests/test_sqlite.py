from datetime import timedelta

from linkedin_agent.adapters.sqlite import (
    Database,
    SqliteAccountStore,
    SqliteActionLog,
    SqliteLeadStore,
    SqliteReviewQueue,
    SqliteTaskQueue,
)
from linkedin_agent.adapters.sqlite.db import SCHEMA_VERSION
from linkedin_agent.core import sequence as seq
from linkedin_agent.models import Action, GovernorState, LeadStage, Task, TaskResult, TaskStatus
from tests.conftest import NOW, make_campaign, make_lead


def task(**kw):
    base = dict(
        action=Action.VISIT,
        profile_url="https://www.linkedin.com/in/x/",
        account="default",
        created_at=NOW,
    )
    base.update(kw)
    return Task(**base)


async def test_schema_migrates_idempotently(tmp_path):
    d = await Database(tmp_path / "a.db").open()
    await d.migrate()
    row = await d.fetchone("SELECT version FROM schema_version")
    assert row["version"] == SCHEMA_VERSION
    await d.close()


async def test_schema_upgrades_a_version_1_database(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """CREATE TABLE leads (id TEXT PRIMARY KEY, campaign TEXT NOT NULL, linkedin_url TEXT NOT NULL UNIQUE,
           first_name TEXT, last_name TEXT, company TEXT, title TEXT, email TEXT, location TEXT, timezone TEXT,
           custom_fields TEXT NOT NULL DEFAULT '{}', profile TEXT NOT NULL DEFAULT '{}',
           posts TEXT NOT NULL DEFAULT '[]', stage TEXT NOT NULL DEFAULT 'new', invited_at TEXT,
           connected_at TEXT, last_touch_at TEXT, last_message_at TEXT, last_message_text TEXT,
           replied_at TEXT, created_at TEXT NOT NULL);
           CREATE TABLE schema_version (version INTEGER NOT NULL);
           INSERT INTO schema_version(version) VALUES (1);
           INSERT INTO leads(id, campaign, linkedin_url, created_at)
             VALUES ('l1', 'c', 'https://www.linkedin.com/in/old', '2026-01-01T00:00:00+00:00');"""
    )
    raw.commit()
    raw.close()
    d = await Database(path).open()
    assert (await d.fetchone("SELECT version FROM schema_version"))["version"] == SCHEMA_VERSION
    cols = {r["name"] for r in await d.fetchall("PRAGMA table_info(leads)")}
    assert "prior_reply_text" in cols
    lead = await SqliteLeadStore(d).get("l1")
    assert lead is not None and lead.prior_reply_text is None
    lead.prior_reply_text = "Thanks Alex"
    await SqliteLeadStore(d).update(lead)
    assert (await SqliteLeadStore(d).get("l1")).prior_reply_text == "Thanks Alex"
    await d.migrate()  # idempotent on an upgraded database
    await d.close()


async def test_queue_claim_respects_windows_and_order(db):
    q = SqliteTaskQueue(db)
    later = task(not_before=NOW + timedelta(hours=1))
    expired = task(not_after=NOW - timedelta(minutes=1))
    first = task(created_at=NOW - timedelta(minutes=2))
    second = task(created_at=NOW - timedelta(minutes=1))
    for t in (later, expired, second, first):
        await q.enqueue(t)
    c1 = await q.claim_next("default", NOW)
    assert c1.id == first.id and c1.status == TaskStatus.RUNNING and c1.attempts == 1
    c2 = await q.claim_next("default", NOW)
    assert c2.id == second.id
    assert await q.claim_next("default", NOW) is None  # later and expired are not claimable
    assert await q.claim_next("other", NOW) is None
    assert (await q.claim_next("default", NOW + timedelta(hours=2))).id == later.id


async def test_queue_claim_specific_and_finish(db):
    q = SqliteTaskQueue(db)
    t = task()
    await q.enqueue(t)
    assert await q.claim("nope", NOW) is None
    c = await q.claim(t.id, NOW)
    assert c.status == TaskStatus.RUNNING
    assert await q.claim(t.id, NOW) is None  # already running
    await q.finish(t.id, TaskResult(status="ok", data={"x": 1}), TaskStatus.DONE)
    got = await q.get(t.id)
    assert got.status == TaskStatus.DONE and got.result["data"] == {"x": 1} and got.finished_at


async def test_queue_expire_and_requeue_stale(db):
    q = SqliteTaskQueue(db)
    overdue = task(not_after=NOW - timedelta(seconds=1))
    fine = task(not_after=NOW + timedelta(hours=1))
    await q.enqueue(overdue)
    await q.enqueue(fine)
    assert await q.expire_overdue(NOW) == 1
    assert (await q.get(overdue.id)).status == TaskStatus.SKIPPED
    running = await q.claim_next("default", NOW - timedelta(hours=2))
    assert running.id == fine.id
    assert await q.requeue_stale_running(NOW, older_than_s=1800) == 1
    assert (await q.get(fine.id)).status == TaskStatus.QUEUED


async def test_queue_open_tasks_counts_depth_and_body_guard(db):
    q = SqliteTaskQueue(db)
    a = task(lead_id=None, step_id="s1", action=Action.MESSAGE, body_hash="h1")
    await q.enqueue(a)
    assert await q.count_open("default", Action.MESSAGE) == 1
    assert await q.count_open("default", Action.VISIT) == 0
    await q.claim(a.id, NOW)
    await q.finish(a.id, TaskResult(status="sent"), TaskStatus.DONE)
    assert await q.body_sent_recently("default", "h1", NOW - timedelta(days=7))
    assert not await q.body_sent_recently("default", "h2", NOW - timedelta(days=7))
    depth = await q.depth("default")
    assert depth["done"] == 1 and depth["queued"] == 0
    assert (await q.recent(5))[0].id == a.id


async def test_queue_cancel_open_for_leads(db):
    q = SqliteTaskQueue(db)
    leads = SqliteLeadStore(db)
    lead = make_lead()
    await leads.upsert_many([lead])
    t = task(lead_id=lead.id, step_id="warm.visit")
    await q.enqueue(t)
    assert await q.open_task_for(lead.id, "warm.visit") is not None
    assert await q.cancel_open_for_leads([lead.id]) == 1
    assert await q.open_task_for(lead.id, "warm.visit") is None
    assert await q.cancel_open_for_leads([]) == 0


async def test_leads_upsert_find_update_and_sequences(db):
    store = SqliteLeadStore(db)
    lead = make_lead()
    ins, upd = await store.upsert_many([lead])
    assert (ins, upd) == (1, 0)
    again = make_lead(first_name="Janet", title=None, custom_fields={"k": "v"})
    ins, upd = await store.upsert_many([again])
    assert (ins, upd) == (0, 1) and again.id == lead.id
    got = await store.get(lead.id)
    assert (
        got.first_name == "Janet"
        and got.title == "VP Engineering"
        and got.custom_fields == {"k": "v"}
    )
    assert (await store.find("janedoe")).id == lead.id
    assert (await store.find("https://www.linkedin.com/in/janedoe/")).id == lead.id
    assert (await store.find("Janet Doe")).id == lead.id
    assert await store.find("nobody") is None

    got.stage = LeadStage.INVITED
    got.invited_at = NOW
    got.posts[0].liked = True
    await store.update(got)
    back = await store.get(lead.id)
    assert back.stage == LeadStage.INVITED and back.invited_at == NOW and back.posts[0].liked

    camp = make_campaign()
    s = seq.new_sequence(back, camp, NOW)
    await store.save_sequence(s)
    due = await store.due_sequences(NOW)
    assert len(due) == 1 and due[0][1].step_id == "warm.visit"
    assert await store.due_sequences(NOW - timedelta(seconds=1)) == []
    s.next_due_at = None
    await store.save_sequence(s)
    assert await store.due_sequences(NOW) == []
    assert await store.stage_counts() == {"invited": 1}
    assert [ld.id for ld in await store.by_stage(LeadStage.INVITED)] == [lead.id]


async def test_leads_pause_resume_and_acceptance_sample(db):
    store = SqliteLeadStore(db)
    camp = make_campaign()
    a, b = make_lead(), make_lead(linkedin_url="https://www.linkedin.com/in/bob/", first_name="Bob")
    await store.upsert_many([a, b])
    for ld in (a, b):
        await store.save_sequence(seq.new_sequence(ld, camp, NOW))
    assert await store.pause_campaign("test") == 2
    assert await store.due_sequences(NOW) == []
    assert await store.is_paused("test")
    assert await store.resume_campaign("test", NOW) == 2
    assert len(await store.due_sequences(NOW)) == 2
    a.invited_at, a.connected_at = NOW - timedelta(days=10), NOW - timedelta(days=8)
    b.invited_at = NOW - timedelta(days=10)
    await store.update(a)
    await store.update(b)
    assert await store.acceptance_sample(NOW - timedelta(days=21), NOW - timedelta(days=3)) == (
        2,
        1,
    )
    assert set(await store.lead_ids_for_campaign("test")) == {a.id, b.id}


async def test_action_log_counts_and_touches(db):
    log = SqliteActionLog(db)
    await log.record("default", Action.CONNECT, "L1", True, "sent", NOW - timedelta(hours=25))
    await log.record("default", Action.CONNECT, "L1", False, "error", NOW)
    await log.record("default", Action.CHECK_CONNECTION, "L1", True, "pending", NOW)
    await log.record("default", Action.VISIT, "L1", True, "ok", NOW - timedelta(hours=30))
    assert (
        await log.count("default", Action.CONNECT, NOW - timedelta(days=2)) == 1
    )  # failures don't count
    assert await log.count("default", Action.CONNECT, NOW - timedelta(hours=1)) == 0
    assert await log.touches("L1", NOW - timedelta(hours=24)) == 0  # check is not a touch
    assert await log.touches("L1", NOW - timedelta(hours=48)) == 2
    recent = await log.recent("default", None, 10)
    assert len(recent) == 4
    assert (
        await log.count_between(
            "default",
            Action.CONNECT,
            NOW - timedelta(days=2),
            NOW + timedelta(days=1),
            ok_only=False,
        )
        == 2
    )


async def test_accounts_roundtrip(db):
    store = SqliteAccountStore(db)
    acct = await store.get("default")
    assert acct.governor_state == GovernorState.NORMAL and acct.first_action_at is None
    acct.first_action_at = NOW
    acct.tripped_until = NOW + timedelta(hours=48)
    acct.trip_reason = "test"
    acct.governor_state = GovernorState.HALVED
    acct.consecutive_failures = 2
    await store.save(acct)
    back = await store.get("default")
    assert (
        back.tripped_until == NOW + timedelta(hours=48)
        and back.governor_state == GovernorState.HALVED
    )
    assert back.consecutive_failures == 2 and back.trip_reason == "test"


async def test_review_queue(db):
    q = SqliteTaskQueue(db)
    r = SqliteReviewQueue(db)
    t = task(action=Action.COMMENT_POST, status=TaskStatus.AWAITING_REVIEW)
    await q.enqueue(t)
    await r.submit(t.id, "comment", {"lead": "Jane"}, "Draft one.")
    items = await r.pending()
    assert (
        len(items) == 1 and items[0].draft == "Draft one." and items[0].context == {"lead": "Jane"}
    )
    await r.decide(t.id, "Edited.", NOW)
    assert await r.pending() == []
    assert (await r.get(t.id)).approved_text == "Edited."
    await r.submit(t.id, "comment", {}, "Draft two.")  # resubmit resets the decision
    assert len(await r.pending()) == 1
