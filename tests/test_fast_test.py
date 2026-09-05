"""fast_test lifts windows and spacing for compressed end-to-end runs; caps still apply."""

from datetime import timedelta

from linkedin_agent.core import sequence as seqeng
from linkedin_agent.core.runner import pacing_delay, tick_interval
from linkedin_agent.models import Action
from linkedin_agent.scheduler import tick
from tests.conftest import NOW, make_campaign, make_lead


def test_build_task_without_windows_is_immediate():
    camp = make_campaign()
    lead = make_lead(timezone="America/New_York")
    t = seqeng.build_task(
        camp.step("invite.posts"), lead, camp, "default", NOW, respect_windows=False
    )
    assert t.not_before == NOW and t.not_after is None


async def test_fast_test_skips_spacing_and_windows(deps):
    deps.settings.fast_test = True
    lead = make_lead(timezone="America/New_York")
    await deps.leads.upsert_many([lead])
    seq = seqeng.new_sequence(lead, deps.campaigns["test"], NOW)
    seq.step_id, seq.branch, seq.next_due_at = "invite.posts", "posts", NOW
    await deps.leads.save_sequence(seq)
    await deps.log.record("default", Action.VISIT, lead.id, True, "ok", NOW - timedelta(hours=1))
    rep = await tick(deps, "default", NOW)
    assert rep.materialized == 1 and rep.deferred == 0
    t = await deps.queue.open_task_for(lead.id, "invite.posts")
    assert t.not_before == NOW and t.not_after is None


async def test_fast_test_keeps_caps(deps):
    deps.settings.fast_test = True
    for i in range(7):
        ld = make_lead(linkedin_url=f"https://www.linkedin.com/in/c{i}/", first_name=f"C{i}")
        await deps.leads.upsert_many([ld])
        seq = seqeng.new_sequence(ld, deps.campaigns["test"], NOW)
        seq.step_id, seq.branch, seq.next_due_at = "invite.posts", "posts", NOW
        await deps.leads.save_sequence(seq)
    rep = await tick(deps, "default", NOW)
    assert rep.materialized == 5 and rep.deferred == 2  # week-1 ramp: 20 * 0.25


def test_fast_test_pacing_and_tick(deps):
    deps.settings.fast_test = True
    deps.settings.tick_interval_s = 300
    assert 5 <= pacing_delay(deps, Action.CONNECT) <= 12
    assert tick_interval(deps) == 20
    deps.settings.fast_test = False
    deps.settings.tick_interval_s = 300
    assert tick_interval(deps) == 300
