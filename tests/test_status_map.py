from linkedin_agent.core.status_map import (
    apply_result,
    is_cannot_contact,
    is_soft_skip,
    is_success,
    normalize_reply_check,
    normalize_status,
)
from linkedin_agent.models import Action, LeadStage, PostRef, TaskResult
from tests.conftest import NOW, make_lead


def test_visit_merges_profile_and_posts_and_timezone():
    lead = make_lead(posts=[], profile={}, title=None, company=None, timezone=None, location=None)
    data = {
        "full_name": "Jane Doe",
        "headline": "VP Eng",
        "title": "VP Engineering",
        "company": "Acme",
        "location": "Berlin, Germany",
        "about": "x" * 600,
        "connection_degree": "2nd",
        "posts": [
            {"url": "https://www.linkedin.com/posts/a", "posted_days_ago": "3", "text": "First"},
            {"url": "", "posted_days_ago": None, "text": "Second"},
            "plain string post",
        ],
    }
    out = apply_result(lead, Action.VISIT, TaskResult(status="ok", data=data), NOW, "UTC")
    assert out.stage == LeadStage.WARMING
    assert out.title == "VP Engineering" and out.company == "Acme"
    assert out.location == "Berlin, Germany" and out.timezone == "Europe/Berlin"
    assert out.profile["headline"] == "VP Eng"
    assert [p.text for p in out.posts] == ["First", "Second", "plain string post"]
    assert out.posts[0].posted_days_ago == 3 and out.posts[1].posted_days_ago is None
    assert out.last_touch_at == NOW
    # the original is untouched
    assert lead.stage == LeadStage.NEW and lead.posts == []


def test_visit_keeps_existing_title_and_liked_flags():
    lead = make_lead()
    lead.posts[0].liked = True
    data = {
        "title": "Other",
        "posts": [{"url": lead.posts[0].url, "posted_days_ago": 2, "text": "same"}],
    }
    out = apply_result(lead, Action.VISIT, TaskResult(status="ok", data=data), NOW)
    assert out.title == "VP Engineering"
    assert out.posts[0].liked is True


def test_connect_results():
    lead = make_lead()
    sent = apply_result(lead, Action.CONNECT, TaskResult(status="sent"), NOW)
    assert sent.stage == LeadStage.INVITED and sent.invited_at == NOW and sent.last_touch_at == NOW
    already = apply_result(lead, Action.CONNECT, TaskResult(status="already_connected"), NOW)
    assert already.stage == LeadStage.CONNECTED and already.connected_at == NOW
    pending = apply_result(lead, Action.CONNECT, TaskResult(status="already_pending"), NOW)
    assert pending.stage == LeadStage.INVITED and pending.last_touch_at is None
    cannot = apply_result(lead, Action.CONNECT, TaskResult(status="cannot_connect"), NOW)
    assert cannot.stage == LeadStage.CANNOT_CONTACT


def test_check_connection_and_messages_and_replies():
    lead = make_lead(stage=LeadStage.INVITED, invited_at=NOW)
    conn = apply_result(lead, Action.CHECK_CONNECTION, TaskResult(status="connected"), NOW)
    assert conn.stage == LeadStage.CONNECTED and conn.connected_at == NOW
    still = apply_result(lead, Action.CHECK_CONNECTION, TaskResult(status="pending"), NOW)
    assert still.stage == LeadStage.INVITED and still.connected_at is None
    msgd = apply_result(conn, Action.MESSAGE, TaskResult(status="sent"), NOW)
    assert msgd.stage == LeadStage.MESSAGING and msgd.last_message_at == NOW
    rep = apply_result(msgd, Action.CHECK_REPLIES, TaskResult(status="replied"), NOW)
    assert rep.stage == LeadStage.REPLIED and rep.replied_at == NOW
    # a later check never regresses a replied lead
    again = apply_result(rep, Action.CHECK_CONNECTION, TaskResult(status="connected"), NOW)
    assert again.stage == LeadStage.REPLIED


def test_message_cannot_contact_and_withdraw():
    lead = make_lead(stage=LeadStage.CONNECTED)
    nc = apply_result(lead, Action.MESSAGE, TaskResult(status="not_connected"), NOW)
    assert nc.stage == LeadStage.CANNOT_CONTACT
    wd = apply_result(
        make_lead(stage=LeadStage.INVITED),
        Action.WITHDRAW_INVITE,
        TaskResult(status="withdrawn"),
        NOW,
    )
    assert wd.stage == LeadStage.NOT_ACCEPTED


def test_like_and_comment_mark_posts():
    lead = make_lead()
    liked = apply_result(
        lead,
        Action.LIKE_POST,
        TaskResult(status="liked", data={"post_url": lead.posts[1].url}),
        NOW,
    )
    assert liked.posts[1].liked and not liked.posts[0].liked
    unknown = apply_result(
        lead,
        Action.COMMENT_POST,
        TaskResult(status="commented", data={"post_url": "https://x/other"}),
        NOW,
    )
    assert unknown.posts[0].commented  # falls back to the newest post


def test_paused_lead_never_changes_stage():
    lead = make_lead(stage=LeadStage.PAUSED)
    out = apply_result(lead, Action.CONNECT, TaskResult(status="sent"), NOW)
    assert out.stage == LeadStage.PAUSED


def test_success_soft_skip_cannot_contact_tables():
    assert is_success(Action.FOLLOW, TaskResult(status="already_following"))
    assert not is_success(Action.FOLLOW, TaskResult(status="cannot_follow"))
    assert is_soft_skip(Action.FOLLOW, TaskResult(status="cannot_follow"))
    assert is_cannot_contact(Action.INMAIL, TaskResult(status="cannot_message"))
    assert not is_success(Action.MESSAGE, TaskResult(status="failed"))


def test_normalize_reply_check_keeps_a_real_reply():
    lead = make_lead(prior_reply_text="Thanks Alex")
    r = TaskResult(
        status="replied", data={"reply_after_ours": True, "last_reply_text": "Sure, tell me more"}
    )
    assert normalize_reply_check(r, lead).status == "replied"
    # no data at all: trusted as before
    assert normalize_reply_check(TaskResult(status="replied"), lead).status == "replied"
    assert normalize_reply_check(TaskResult(status="none"), lead).status == "none"


def test_normalize_reply_check_downgrades_by_position():
    r = TaskResult(status="replied", data={"reply_after_ours": False, "last_reply_text": "x"})
    out = normalize_reply_check(r, make_lead())
    assert out.status == "none" and out.data["stale_reply_ignored"] == "position"


def test_normalize_reply_check_downgrades_a_reply_that_predates_our_send():
    lead = make_lead(prior_reply_text="Thanks Alex")
    r = TaskResult(status="replied", data={"last_reply_text": "Thanks  Alex"})
    out = normalize_reply_check(r, lead)
    assert out.status == "none" and "prior_reply_text" in out.data["stale_reply_ignored"]
    # a longer quote that starts with the known text is the same message
    r2 = TaskResult(status="replied", data={"last_reply_text": "Thanks Alex, will do."})
    assert normalize_reply_check(r2, lead).status == "none"
    # a different message from them is a real reply
    r3 = TaskResult(status="replied", data={"last_reply_text": "Happy to chat next week"})
    assert normalize_reply_check(r3, lead).status == "replied"
    # without a known prior reply there is nothing to compare against
    assert normalize_reply_check(r, make_lead()).status == "replied"


def test_parse_posts_drops_urls_that_are_not_posts():
    from linkedin_agent.core.status_map import _parse_posts

    posts = _parse_posts(
        [
            {"url": "https://www.linkedin.com/in/marisa/", "posted_days_ago": 1, "text": "Hiring!"},
            {"url": "https://www.linkedin.com/jobs/view/123/", "posted_days_ago": 2, "text": "Job"},
            {
                "url": "https://www.linkedin.com/posts/marisa_ai-activity-7100_abc",
                "posted_days_ago": 3,
                "text": "Real",
            },
            {
                "url": "https://www.linkedin.com/feed/update/urn:li:activity:7100/",
                "text": "Also real",
            },
            {"url": "  ", "text": "No link"},
        ]
    )
    assert [p.url for p in posts] == [
        "",
        "",
        "https://www.linkedin.com/posts/marisa_ai-activity-7100_abc",
        "https://www.linkedin.com/feed/update/urn:li:activity:7100/",
        "",
    ]
    assert [p.text for p in posts] == ["Hiring!", "Job", "Real", "Also real", "No link"]


def test_clean_profile_normalises_every_field():
    from linkedin_agent.core.status_map import clean_profile

    out = clean_profile(
        {
            "full_name": "  Marisa   Doe ",
            "headline": "x" * 400,
            "about": "a" * 3000,
            "connection_degree": "2nd degree connection",
            "company_page_url": "https://www.linkedin.com/company/acme/?trk=abc",
            "mutual_connections": "12 mutual connections",
            "title": "",
            "unexpected": "ignored",
        }
    )
    assert out["full_name"] == "Marisa Doe"
    assert len(out["headline"]) == 300 and len(out["about"]) == 2000
    assert out["connection_degree"] == "2nd"
    assert out["company_page_url"] == "https://www.linkedin.com/company/acme/"
    assert out["mutual_connections"] == 12
    assert "title" not in out and "unexpected" not in out


def test_clean_profile_drops_what_it_cannot_trust():
    from linkedin_agent.core.status_map import clean_profile

    out = clean_profile(
        {
            "company_page_url": "https://www.linkedin.com/in/marisa/",  # a person, not a company
            "connection_degree": "connected",
            "mutual_connections": "many",
            "location": None,
        }
    )
    assert out == {}
    assert clean_profile({"company_page_url": "https://evil.com/company/acme"}) == {}
    assert clean_profile({"connection_degree": "1"})["connection_degree"] == "1st"
    assert clean_profile({"mutual_connections": -3}) == {}


def test_like_result_with_bogus_post_url_marks_the_newest_post():
    lead = make_lead(
        posts=[
            PostRef(url="https://www.linkedin.com/posts/a_1", posted_days_ago=1, text="new"),
            PostRef(url="https://www.linkedin.com/posts/a_2", posted_days_ago=5, text="old"),
        ]
    )
    liked = apply_result(
        lead,
        Action.LIKE_POST,
        TaskResult(status="liked", data={"post_url": lead.linkedin_url}),
        NOW,
    )
    assert liked.posts[0].liked is True and liked.posts[1].liked is False
    commented = apply_result(
        lead,
        Action.COMMENT_POST,
        TaskResult(status="commented", data={"post_url": "https://www.linkedin.com/posts/a_2"}),
        NOW,
    )
    assert commented.posts[1].commented is True and commented.posts[0].commented is False


def test_normalize_status_snaps_a_commented_status_to_the_known_one():
    r = normalize_status(Action.LIKE_POST, TaskResult(status="liked_but_url_not_found"))
    assert r.status == "liked" and r.data["reported_status"] == "liked_but_url_not_found"
    r = normalize_status(Action.CONNECT, TaskResult(status="sent_without_note", data={"k": 1}))
    assert r.status == "sent" and r.data == {"k": 1, "reported_status": "sent_without_note"}
    # the longest known prefix wins: already_liked, not liked
    r = normalize_status(Action.LIKE_POST, TaskResult(status="already_liked_earlier"))
    assert r.status == "already_liked"


def test_normalize_status_leaves_known_statuses_alone():
    for action, status in (
        (Action.LIKE_POST, "liked"),
        (Action.LIKE_POST, "post_not_found"),
        (Action.CONNECT, "cannot_connect"),
        (Action.CHECK_CONNECTION, "no_option"),
        (Action.MESSAGE, "sent"),
    ):
        r = TaskResult(status=status, data={"x": 1})
        assert normalize_status(action, r) is r


def test_normalize_status_turns_gibberish_into_a_retryable_failure():
    r = normalize_status(Action.FOLLOW, TaskResult(status="done_i_think", error="?"))
    assert r.status == "failed"
    assert "unknown status 'done_i_think'" in r.error and r.error.endswith(": ?")
    assert r.data["reported_status"] == "done_i_think"
    # "likedd" is not "liked" followed by a separator
    assert normalize_status(Action.LIKE_POST, TaskResult(status="likedd")).status == "failed"
