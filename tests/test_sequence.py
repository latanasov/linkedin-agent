import random
from datetime import timedelta

from linkedin_agent.core import sequence as seq
from linkedin_agent.models import Action, LeadStage, PostRef, TaskResult
from tests.conftest import NOW, make_campaign, make_lead

RNG = random.Random(1)


def test_decide_branch():
    camp = make_campaign()
    assert seq.decide_branch(make_lead(), camp) == "posts"
    assert seq.decide_branch(make_lead(posts=[]), camp) == "quiet"
    old = make_lead(posts=[PostRef(text="x", posted_days_ago=45)])
    assert seq.decide_branch(old, camp) == "quiet"
    unknown_age = make_lead(posts=[PostRef(text="x")])
    assert seq.decide_branch(unknown_age, camp) == "posts"


def test_new_sequence_starts_at_first_step():
    s = seq.new_sequence(make_lead(), make_campaign(), NOW)
    assert s.step_id == "warm.visit" and s.next_due_at == NOW and s.branch is None


def test_jitter_bounds():
    for _ in range(50):
        j = seq.jitter(timedelta(days=1), RNG)
        assert timedelta(days=1) <= j <= timedelta(days=1.4)
    assert seq.jitter(timedelta(0), RNG) == timedelta(0)


def test_advance_walks_the_posts_branch():
    camp = make_campaign()
    s = seq.new_sequence(make_lead(), camp, NOW)
    s.branch = "posts"
    a = seq.advance(s, camp, camp.step("warm.visit"), TaskResult(status="ok"), NOW, RNG)
    assert a.seq.step_id == "warm.follow"
    assert NOW + timedelta(days=1) <= a.seq.next_due_at <= NOW + timedelta(days=1.4)
    a = seq.advance(a.seq, camp, camp.step("warm.follow"), TaskResult(status="followed"), NOW, RNG)
    assert a.seq.step_id == "warm.like"
    a = seq.advance(a.seq, camp, camp.step("warm.like"), TaskResult(status="liked"), NOW, RNG)
    assert a.seq.step_id == "warm.comment"
    a = seq.advance(
        a.seq, camp, camp.step("warm.comment"), TaskResult(status="commented"), NOW, RNG
    )
    assert a.seq.step_id == "invite.posts"  # invite.quiet skipped by branch
    assert len(a.seq.history) == 4


def test_advance_quiet_branch_skips_like_comment_and_posts_invite():
    camp = make_campaign()
    s = seq.new_sequence(make_lead(posts=[]), camp, NOW)
    s.branch = "quiet"
    a = seq.advance(s, camp, camp.step("warm.visit"), TaskResult(status="ok"), NOW, RNG)
    assert a.seq.step_id == "warm.follow"
    a = seq.advance(a.seq, camp, camp.step("warm.follow"), TaskResult(status="followed"), NOW, RNG)
    assert a.seq.step_id == "invite.quiet"


def test_on_result_routing_and_repeat_until_timeout():
    camp = make_campaign()
    s = seq.new_sequence(make_lead(), camp, NOW)
    s.step_id, s.branch, s.step_entered_at = "invite.posts", "posts", NOW
    a = seq.advance(s, camp, camp.step("invite.posts"), TaskResult(status="sent"), NOW, RNG)
    assert a.seq.step_id == "wait.accept" and a.seq.step_entered_at == NOW
    # pending repeats the same step daily
    a = seq.advance(
        a.seq,
        camp,
        camp.step("wait.accept"),
        TaskResult(status="pending"),
        NOW + timedelta(days=1),
        RNG,
    )
    assert a.seq.step_id == "wait.accept" and a.seq.step_entered_at == NOW
    assert a.seq.next_due_at >= NOW + timedelta(days=2)
    # after 21 days, timeout routes to withdraw regardless of the pending result
    late = NOW + timedelta(days=21)
    a = seq.advance(a.seq, camp, camp.step("wait.accept"), TaskResult(status="pending"), late, RNG)
    assert a.seq.step_id == "withdraw"
    a = seq.advance(a.seq, camp, camp.step("withdraw"), TaskResult(status="withdrawn"), late, RNG)
    assert a.ended_stage == LeadStage.NOT_ACCEPTED and a.seq.step_id is None


def test_already_connected_shortcuts_to_first_message():
    camp = make_campaign()
    s = seq.new_sequence(make_lead(), camp, NOW)
    s.step_id, s.branch = "invite.posts", "posts"
    a = seq.advance(
        s, camp, camp.step("invite.posts"), TaskResult(status="already_connected"), NOW, RNG
    )
    assert a.seq.step_id == "post.m1" and a.seq.next_due_at == NOW


def test_reply_ends_sequence_and_no_reply_continues():
    camp = make_campaign()
    s = seq.new_sequence(make_lead(), camp, NOW)
    s.step_id, s.branch = "post.r1", "posts"
    a = seq.advance(s, camp, camp.step("post.r1"), TaskResult(status="replied"), NOW, RNG)
    assert a.ended_stage == LeadStage.REPLIED
    b = seq.advance(s, camp, camp.step("post.r1"), TaskResult(status="none"), NOW, RNG)
    assert b.seq.step_id == "post.m2"
    s.step_id = "post.r3"
    c = seq.advance(s, camp, camp.step("post.r3"), TaskResult(status="none"), NOW, RNG)
    assert c.ended_stage == LeadStage.NURTURE


def test_cannot_contact_ends_sequence():
    camp = make_campaign()
    s = seq.new_sequence(make_lead(), camp, NOW)
    s.step_id, s.branch = "post.m1", "posts"
    a = seq.advance(s, camp, camp.step("post.m1"), TaskResult(status="not_connected"), NOW, RNG)
    assert a.ended_stage == LeadStage.CANNOT_CONTACT


def test_hard_failure_stalls_sequence():
    camp = make_campaign()
    s = seq.new_sequence(make_lead(), camp, NOW)
    s.step_id, s.branch = "post.m1", "posts"
    a = seq.advance(s, camp, camp.step("post.m1"), TaskResult(status="failed"), NOW, RNG)
    assert a.seq.step_id == "post.m1" and a.seq.next_due_at is None and a.routed_to is None


def test_soft_skip_moves_on():
    camp = make_campaign()
    s = seq.new_sequence(make_lead(), camp, NOW)
    s.step_id, s.branch = "warm.like", "posts"
    a = seq.advance(s, camp, camp.step("warm.like"), TaskResult(status="post_not_found"), NOW, RNG)
    assert a.seq.step_id == "warm.comment"


def test_skip_current_and_end_of_sequence():
    camp = make_campaign()
    s = seq.new_sequence(make_lead(), camp, NOW)
    s.step_id, s.branch = "post.r3", "posts"
    a = seq.skip_current(s, camp, NOW, RNG)
    assert a.ended_stage == LeadStage.DONE and a.seq.step_id is None
    s.step_id = "warm.like"
    b = seq.skip_current(s, camp, NOW, RNG)
    assert b.seq.step_id == "warm.comment"


def test_pick_post():
    lead = make_lead()
    assert seq.pick_post(lead, "newest").posted_days_ago == 2
    lead.posts[0].liked = True
    assert seq.pick_post(lead, "different_from_liked").posted_days_ago == 9
    lead.posts[1].commented = True
    # everything liked or commented: fall back to any un-commented post
    assert seq.pick_post(lead, "different_from_liked").posted_days_ago == 2
    lead.posts[0].commented = True
    assert seq.pick_post(lead, "different_from_liked") is None
    assert seq.pick_post(make_lead(posts=[]), "newest") is None


def test_build_task_snaps_to_window_in_lead_timezone():
    camp = make_campaign()
    lead = make_lead(timezone="America/New_York")
    # NOW is Wednesday 10:00 UTC = 06:00 NY -> send window opens 08:30 NY = 12:30 UTC
    t = seq.build_task(camp.step("invite.posts"), lead, camp, "default", NOW, {"note": ""})
    assert t.not_before.hour == 12 and t.not_before.minute == 30
    assert t.not_after.hour == 15
    assert t.action == Action.CONNECT and t.step_id == "invite.posts" and t.lead_id == lead.id
    # 'any' window right now
    t2 = seq.build_task(camp.step("warm.visit"), make_lead(), camp, "default", NOW)
    assert t2.not_before == NOW
