from datetime import timedelta

from linkedin_agent.core import sequence as seqeng
from linkedin_agent.core.runner import process_task
from linkedin_agent.models import Action, GovernorState, LeadStage, TaskStatus
from linkedin_agent.scheduler import (
    resolve_review,
    restart_lead,
    retry_lead,
    skip_lead_step,
    tick,
    update_governor,
)
from tests.conftest import NOW, make_lead


async def add_lead(deps, lead=None, step=None, branch=None, due=NOW, **overrides):
    lead = lead or make_lead(**overrides)
    await deps.leads.upsert_many([lead])
    camp = deps.campaigns["test"]
    s = seqeng.new_sequence(lead, camp, due)
    if step:
        s.step_id, s.branch = step, branch
    s.next_due_at = due
    await deps.leads.save_sequence(s)
    return lead


async def test_tick_materializes_visit_for_new_lead(deps):
    lead = await add_lead(deps, posts=[])
    rep = await tick(deps, "default", NOW)
    assert rep.materialized == 1
    t = await deps.queue.open_task_for(lead.id, "warm.visit")
    assert t is not None and t.action == Action.VISIT and t.params["lead_name"] == "Jane Doe"
    # second tick does not duplicate
    rep2 = await tick(deps, "default", NOW)
    assert rep2.materialized == 0


async def test_tick_skips_branch_mismatch_and_missing_posts(deps):
    quiet = await add_lead(deps, step="warm.like", branch="quiet", posts=[])
    rep = await tick(deps, "default", NOW)
    seq = await deps.leads.get_sequence(quiet.id)
    assert (
        seq.step_id == "invite.quiet" and rep.skipped_steps == 1
    )  # like+comment skipped in one hop
    assert rep.materialized == 1  # the quiet invite itself is scheduled (Wednesday, send window)
    t = await deps.queue.open_task_for(quiet.id, "invite.quiet")
    assert t.params["note_template"] == "connection_note_quiet"


async def test_tick_picks_post_for_like_and_comment(deps):
    lead = await add_lead(deps, step="warm.like", branch="posts")
    await tick(deps, "default", NOW)
    t = await deps.queue.open_task_for(lead.id, "warm.like")
    assert t.params["post_url"] == lead.posts[0].url and t.params["post_text"].startswith("We cut")


async def test_tick_defers_when_spacing_violated(deps):
    lead = await add_lead(deps, step="warm.follow", branch="posts")
    await deps.log.record("default", Action.VISIT, lead.id, True, "ok", NOW - timedelta(hours=2))
    rep = await tick(deps, "default", NOW)
    assert rep.materialized == 0 and rep.deferred == 1


async def test_tick_respects_caps_including_open_tasks(deps):
    acct = await deps.accounts.get("default")
    acct.first_action_at = NOW - timedelta(days=60)
    await deps.accounts.save(acct)
    for i in range(25):
        await add_lead(
            deps,
            linkedin_url=f"https://www.linkedin.com/in/p{i}/",
            first_name=f"P{i}",
            step="invite.posts",
            branch="posts",
        )
    rep = await tick(deps, "default", NOW)
    assert rep.materialized == 20 and rep.deferred == 5  # daily connect cap
    assert await deps.queue.count_open("default", Action.CONNECT) == 20


async def test_tick_gated_when_breaker_tripped(deps):
    await add_lead(deps)
    acct = await deps.accounts.get("default")
    acct.tripped_until = NOW + timedelta(hours=1)
    await deps.accounts.save(acct)
    rep = await tick(deps, "default", NOW)
    assert rep.materialized == 0 and rep.notes


async def test_tick_expires_and_requeues(deps):
    lead = await add_lead(deps, due=NOW + timedelta(days=5))
    camp = deps.campaigns["test"]
    old = seqeng.build_task(camp.step("warm.visit"), lead, camp, "default", NOW - timedelta(days=1))
    await deps.queue.enqueue(old)
    rep = await tick(deps, "default", NOW)
    assert rep.expired == 1


async def test_tick_creates_review_item_when_review_on(deps, llm):
    camp = deps.campaigns["test"]
    camp.review_comments = True
    llm.replies = [
        "Removing the approval step is the part most teams skip. Did the support load move elsewhere?"
    ]
    lead = await add_lead(deps, step="warm.comment", branch="posts")
    rep = await tick(deps, "default", NOW)
    assert rep.reviews == 1 and rep.materialized == 1
    t = await deps.queue.open_task_for(lead.id, "warm.comment")
    assert t.status == TaskStatus.AWAITING_REVIEW
    items = await deps.review.pending()
    assert len(items) == 1 and items[0].context["lead"] == "Jane Doe"
    # approve with an edit → task queued with the edited text
    msg = await resolve_review(deps, t.id, "Edited comment that is specific to the post.", NOW)
    assert msg.startswith("approved")
    t2 = await deps.queue.get(t.id)
    assert t2.status == TaskStatus.QUEUED and t2.params["text"].startswith("Edited")
    claimed = await deps.queue.claim(t2.id, NOW)
    out = await process_task(claimed, deps)
    assert out.status == TaskStatus.DONE


async def test_resolve_review_reject_and_bad_text(deps, llm):
    camp = deps.campaigns["test"]
    camp.review_comments = True
    llm.replies = ["A fine specific comment about the approval step."]
    lead = await add_lead(deps, step="warm.comment", branch="posts")
    await tick(deps, "default", NOW)
    t = await deps.queue.open_task_for(lead.id, "warm.comment")
    assert "not approved" in await resolve_review(deps, t.id, "Great post!", NOW)
    assert "rejected" in await resolve_review(deps, t.id, None, NOW)
    assert (await deps.queue.get(t.id)).status == TaskStatus.SKIPPED
    assert (await deps.leads.get_sequence(lead.id)).step_id == "invite.posts"
    assert "not found" in await resolve_review(deps, "nope", "x", NOW)


async def test_tick_review_draft_rejected_skips_step(deps, llm):
    deps.campaigns["test"].review_comments = True
    llm.replies = ["Great post!", "So true!"]
    lead = await add_lead(deps, step="warm.comment", branch="posts")
    rep = await tick(deps, "default", NOW)
    assert rep.reviews == 0 and any("rejected" in n for n in rep.notes)
    assert (await deps.leads.get_sequence(lead.id)).step_id == "invite.posts"


async def test_governor_updates_daily_from_acceptance(deps):
    for i in range(12):
        ld = make_lead(linkedin_url=f"https://www.linkedin.com/in/g{i}/", first_name=f"G{i}")
        ld.invited_at = NOW - timedelta(days=10)
        if i < 2:
            ld.connected_at = NOW - timedelta(days=8)
        await deps.leads.upsert_many([ld])
        ld2 = await deps.leads.get(ld.id)
        ld2.invited_at, ld2.connected_at = ld.invited_at, ld.connected_at
        await deps.leads.update(ld2)
    msg = await update_governor(deps, "default", NOW)
    assert msg and msg.startswith("paused")
    assert (await deps.accounts.get("default")).governor_state == GovernorState.PAUSED
    assert await update_governor(deps, "default", NOW + timedelta(hours=1)) is None  # once a day


async def test_retry_and_skip_helpers(deps):
    lead = await add_lead(deps, step="post.m1", branch="posts")
    seq = await deps.leads.get_sequence(lead.id)
    seq.next_due_at = None
    await deps.leads.save_sequence(seq)
    assert "re-armed" in await retry_lead(deps, lead, NOW)
    assert (await deps.leads.get_sequence(lead.id)).next_due_at == NOW
    await tick(deps, "default", NOW)
    assert await deps.queue.open_task_for(lead.id, "post.m1") is not None
    assert "skipped post.m1" in await skip_lead_step(deps, lead, NOW)
    assert await deps.queue.open_task_for(lead.id, "post.m1") is None
    assert (await deps.leads.get_sequence(lead.id)).step_id == "post.r1"


async def test_full_sequence_end_to_end_with_fakes(deps, executor, clock):
    """Walk one lead through the whole default playbook with scripted results."""
    lead = await add_lead(deps, posts=[])
    executor.script(
        Action.VISIT,
        {
            "status": "ok",
            "headline": "VP",
            "posts": [
                {
                    "url": "https://www.linkedin.com/posts/a-1",
                    "posted_days_ago": 1,
                    "text": "Post A",
                },
                {
                    "url": "https://www.linkedin.com/posts/b-2",
                    "posted_days_ago": 5,
                    "text": "Post B",
                },
            ],
        },
    )
    checks = iter(["pending", "pending", "connected"])
    executor.script(Action.CHECK_CONNECTION, lambda t: {"status": next(checks)})
    replies = iter(["none", "none", "replied"])
    executor.script(Action.CHECK_REPLIES, lambda t: {"status": next(replies)})

    visited: list[str] = []
    for day in range(60):
        clock.now = NOW + timedelta(days=day)
        # two ticks a day at 10:00 and 15:00 UTC to hit both send slots
        for hour in (10, 15):
            clock.now = clock.now.replace(hour=hour)
            await tick(deps, "default", clock.now)
            while (t := await deps.queue.claim_next("default", clock.now)) is not None:
                out = await process_task(t, deps)
                visited.append(f"{t.step_id}:{out.result.status if out.result else out.status}")
        if (await deps.leads.get(lead.id)).stage == LeadStage.REPLIED:
            break
    steps = [v.split(":")[0] for v in visited]
    assert steps[:4] == ["warm.visit", "warm.follow", "warm.like", "warm.comment"]
    assert "invite.posts" in steps and "post.m1" in steps and "post.m2" in steps
    assert steps.count("wait.accept") == 3
    assert (await deps.leads.get(lead.id)).stage == LeadStage.REPLIED
    assert (await deps.leads.get_sequence(lead.id)).step_id is None
    # spacing: never two touches on the same lead within a day
    touches = [
        e
        for e in await deps.log.recent("default", None, 100)
        if e["action"] in ("visit", "follow", "like_post", "comment_post", "connect", "message")
    ]
    days = [e["at"][:10] for e in touches]
    assert len(days) == len(set(days)), days


# ── restart ───────────────────────────────────────────────────────────────


async def test_restart_from_cannot_contact_resumes_at_chosen_step(deps, executor):
    lead = await add_lead(deps, step="invite.posts", branch="posts")
    executor.script(Action.CONNECT, {"status": "cannot_connect"})
    executor.script(Action.CHECK_CONNECTION, {"status": "no_option"})
    await tick(deps, "default", NOW)
    t = await deps.queue.claim_next("default", NOW)
    await process_task(t, deps)
    assert (await deps.leads.get(lead.id)).stage == LeadStage.CANNOT_CONTACT
    assert (await deps.leads.get_sequence(lead.id)).step_id is None
    assert (await tick(deps, "default", NOW)).materialized == 0

    lead = await deps.leads.get(lead.id)
    msg = await restart_lead(deps, lead, NOW, "wait.accept")
    assert msg.startswith("restarted at wait.accept (stage invited")
    seq = await deps.leads.get_sequence(lead.id)
    assert seq.step_id == "wait.accept" and seq.next_due_at == NOW and seq.branch == "posts"
    assert seq.history[-1]["result"] == "restarted"
    assert (await deps.leads.get(lead.id)).stage == LeadStage.INVITED

    # the scheduler picks it up again, and a later 'connected' moves the lead forward
    rep = await tick(deps, "default", NOW)
    assert rep.materialized == 1
    t = await deps.queue.claim_next("default", NOW)
    assert t.action == Action.CHECK_CONNECTION
    executor.script(Action.CHECK_CONNECTION, {"status": "connected"})
    await process_task(t, deps)
    lead3 = await deps.leads.get(lead.id)
    assert lead3.stage == LeadStage.CONNECTED
    assert (await deps.leads.get_sequence(lead.id)).step_id == "post.m1"


async def test_restart_defaults_to_first_step_and_cancels_open_tasks(deps):
    lead = await add_lead(deps, step="invite.posts", branch="posts")
    await tick(deps, "default", NOW)
    assert await deps.queue.open_task_for(lead.id, "invite.posts") is not None
    lead.stage = LeadStage.NOT_ACCEPTED
    await deps.leads.update(lead)

    msg = await restart_lead(deps, lead, NOW)
    assert "restarted at warm.visit (stage new, 1 queued task(s) cancelled)" == msg
    assert await deps.queue.open_task_for(lead.id, "invite.posts") is None
    seq = await deps.leads.get_sequence(lead.id)
    assert seq.step_id == "warm.visit" and seq.branch is None
    assert (await deps.leads.get(lead.id)).stage == LeadStage.NEW


async def test_restart_rejects_unknown_step_and_missing_campaign(deps):
    lead = await add_lead(deps)
    msg = await restart_lead(deps, lead, NOW, "nope")
    assert msg.startswith("unknown step 'nope'") and "warm.visit" in msg
    assert (await deps.leads.get_sequence(lead.id)).step_id == "warm.visit"
    lead.campaign = "ghost"
    assert "not loaded" in await restart_lead(deps, lead, NOW)


async def test_restart_creates_sequence_when_lead_has_none(deps):
    lead = make_lead()
    await deps.leads.upsert_many([lead])
    assert await deps.leads.get_sequence(lead.id) is None
    await restart_lead(deps, lead, NOW, "post.m1")
    seq = await deps.leads.get_sequence(lead.id)
    assert seq is not None and seq.step_id == "post.m1"
    assert (await deps.leads.get(lead.id)).stage == LeadStage.CONNECTED


# ── the tick survives one bad lead and notices edited campaign files ──────


async def test_tick_isolates_a_lead_whose_step_was_renamed(deps):
    """A step id edited out from under a lead used to raise KeyError and take the whole
    tick, and with it the run, down. Now that lead gets a note and the others proceed."""
    stuck = await add_lead(
        deps, step="gone.step", linkedin_url="https://www.linkedin.com/in/stuck/"
    )
    fine = await add_lead(deps, posts=[], linkedin_url="https://www.linkedin.com/in/fine/")
    rep = await tick(deps, "default", NOW)
    assert rep.materialized == 1
    assert any("gone.step" in n and "no longer exists" in n for n in rep.notes)
    assert await deps.queue.open_task_for(fine.id, "warm.visit") is not None
    assert await deps.queue.open_task_for(stuck.id, "gone.step") is None


async def test_tick_reloads_campaign_files_that_change_on_disk(deps, tmp_path):
    import os

    from linkedin_agent.campaigns import BUILTIN_DIR

    folder = deps.settings.campaigns_dir
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "mine.yaml"
    text = (BUILTIN_DIR / "default.yaml").read_text().replace("name: default", "name: mine")
    path.write_text(text)

    rep = await tick(deps, "default", NOW)  # first tick only records what is on disk
    assert rep.notes == [] and "mine" not in deps.campaigns

    stamp = path.stat().st_mtime + 10
    os.utime(path, (stamp, stamp))
    rep = await tick(deps, "default", NOW)
    assert any("'mine' reloaded" in n for n in rep.notes)
    assert deps.campaigns["mine"].agent_name == "Your Name"

    path.write_text(text.replace('agent_name: "Your Name"', 'agent_name: "Alex"'))
    os.utime(path, (stamp + 10, stamp + 10))
    rep = await tick(deps, "default", NOW)
    assert deps.campaigns["mine"].agent_name == "Alex"

    # a half-finished edit does not validate: the last good version stays in use
    path.write_text("name: mine\nsteps: []\n")
    os.utime(path, (stamp + 20, stamp + 20))
    rep = await tick(deps, "default", NOW)
    assert any("not reloaded" in n for n in rep.notes)
    assert deps.campaigns["mine"].agent_name == "Alex"

    path.unlink()
    rep = await tick(deps, "default", NOW)
    assert any("was removed" in n for n in rep.notes) and "mine" in deps.campaigns
    # the fixture's in-memory campaign never came from disk and is never touched
    assert deps.campaigns["test"].agent_name == "Alex"


async def test_tick_likes_via_the_activity_feed_when_the_stored_post_url_is_poisoned(deps):
    """Before the fix this became a task that failed prompt validation and skipped the
    step with no_content; now the URL is blank and the prompt uses the activity feed."""
    from linkedin_agent.core.tasks import build_prompt
    from linkedin_agent.models import PostRef

    url = "https://www.linkedin.com/in/marisa-rubio-0b1916/"
    lead = await add_lead(
        deps,
        step="warm.like",
        branch="posts",
        linkedin_url=url,
        posts=[PostRef(url=url, text="Great quarter for the team.", posted_days_ago=2)],
    )
    rep = await tick(deps, "default", NOW)
    assert rep.materialized == 1 and rep.skipped_steps == 0
    task = await deps.queue.open_task_for(lead.id, "warm.like")
    assert task is not None and task.params["post_url"] == ""
    build_prompt(task.action, task.profile_url, task.params)  # must not raise
