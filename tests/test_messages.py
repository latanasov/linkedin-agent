import pytest

from linkedin_agent.core import messages as msg
from linkedin_agent.models import Campaign, PostRef
from tests.conftest import FakeLLM, make_campaign, make_lead


def test_fields_for_includes_custom_and_post_topic():
    lead = make_lead(custom_fields={"pain_point": "cloud costs", "custom_region": "EMEA"})
    f = msg.fields_for(lead, make_campaign())
    assert f["first_name"] == "Jane" and f["company"] == "Acme"
    assert f["pain_point"] == "cloud costs" and f["custom_pain_point"] == "cloud costs"
    assert f["region"] == "EMEA" and f["custom_region"] == "EMEA"
    assert f["post_topic"].startswith("We cut onboarding time in half")
    assert f["booking_link"] == "https://cal.com/alex/15min"


def test_topic_from_post():
    assert msg.topic_from_post("Hiring two engineers! More soon.") == "Hiring two engineers!"
    assert msg.topic_from_post("#hiring #growth We are growing fast") == "We are growing fast"
    long = "a" * 30 + " " + "b" * 40 + " " + "c" * 10
    t = msg.topic_from_post(long)
    assert t.endswith("…") and len(t) <= 62
    assert msg.topic_from_post("") == ""


def test_render_fallbacks_and_warnings():
    r = msg.render(
        "Hi {first_name} from {company}, re {post_topic}", {"first_name": "", "company": "Acme"}
    )
    assert r.text == "Hi there from Acme, re your recent post"
    assert any("first_name" in w for w in r.warnings) and any("post_topic" in w for w in r.warnings)


def test_render_drops_line_that_held_only_hook():
    r = msg.render("Hi {first_name},\n{hook}\nQuestion?", {"first_name": "Jane", "hook": ""})
    assert r.text == "Hi Jane,\nQuestion?"
    # a lone {booking_link} line disappears when there is no link; {company} keeps its default
    r2 = msg.render("Slot:\n{booking_link}\nAt {company}", {})
    assert r2.text == "Slot:\nAt your company"


def test_template_fields_and_allowed():
    assert msg.template_fields("Hi {first_name} {hook} {custom_x}") == {
        "first_name",
        "hook",
        "custom_x",
    }
    allowed = msg.allowed_fields(make_campaign(), {"custom_x", "region"})
    assert {"custom_x", "x", "region", "custom_region", "hook", "company"} <= allowed


def test_body_hash_normalises_whitespace_and_case():
    assert msg.body_hash("Hi  Jane\n") == msg.body_hash("hi jane")
    assert msg.body_hash("Hi Jane") != msg.body_hash("Hi Bob")


@pytest.mark.parametrize(
    "text,problem",
    [
        ("", "empty"),
        ("Great post, so true!", "banned phrase"),
        ("Interesting. See https://x.com for more.", "contains a link"),
        ("Check out acme.io today.", "link"),
        ("One. Two. Three. Four.", "4 sentences"),
        ("x" * 601 + ".", "longer than 600"),
    ],
)
def test_check_comment_rejects(text, problem):
    problems = msg.check_comment(text)
    assert any(problem in p for p in problems), problems


def test_check_comment_accepts_specific_comment_and_flags_company_mention():
    good = "Removing the approval step is the part most teams skip. Did the support load move elsewhere?"
    assert msg.check_comment(good) == []
    c = make_campaign(company_name="Northwind")
    assert msg.check_comment("Northwind does this well.", campaign=c)


def test_check_hook():
    assert msg.check_hook("Your point about removing the approval step stuck with me.") == []
    assert msg.check_hook("Did you like it?")
    assert msg.check_hook("One sentence. Two sentences.")
    assert msg.check_hook("I came across your profile and liked it.")
    assert msg.check_hook("x" * 121)


async def test_draft_hook_uses_llm_then_checks_then_falls_back():
    lead = make_lead()
    camp = make_campaign()
    llm = FakeLLM(["Did you enjoy it?", "Your onboarding post was sharp."])
    hook, fallback = await msg.draft_hook(lead, camp, llm)
    assert hook == "Your onboarding post was sharp." and fallback is False
    assert len(llm.prompts) == 2
    bad = FakeLLM(["Question one?", "Question two?"])
    hook, fallback = await msg.draft_hook(lead, camp, bad)
    assert fallback is True and hook == "Noticed your work at Acme."


async def test_draft_hook_without_personal_data_uses_fallback_without_llm_call():
    lead = make_lead(posts=[], profile={})
    llm = FakeLLM()
    hook, fallback = await msg.draft_hook(lead, make_campaign(), llm)
    assert fallback and hook == "Noticed your work at Acme." and llm.prompts == []


async def test_draft_hook_llm_error_falls_back():
    hook, fallback = await msg.draft_hook(make_lead(), make_campaign(), FakeLLM(fail=True))
    assert fallback


async def test_render_message_fills_hook_and_fields():
    camp = make_campaign()
    lead = make_lead()
    r = await msg.render_message("m1", lead, camp, FakeLLM(["Your onboarding post was sharp."]))
    assert r.text.startswith("Hi Jane, thanks for connecting.\nYour onboarding post was sharp.")
    assert r.hook_used and not r.hook_fallback_used


async def test_render_message_hook_none_is_pure_template():
    camp = make_campaign(personalization={"hook": "none", "hook_fallback": ""})
    llm = FakeLLM()
    r = await msg.render_message("m1", make_lead(), camp, llm)
    assert "Your onboarding" not in r.text and llm.prompts == []
    assert r.text.startswith("Hi Jane, thanks for connecting.\nQuick question")


async def test_render_message_without_llm_uses_fallback_line_removed():
    r = await msg.render_message("m1", make_lead(), make_campaign(), None)
    assert not r.hook_used and "Noticed" not in r.text


async def test_render_message_warns_when_long():
    camp = make_campaign(messages={**make_campaign().messages, "m1": "x" * 350})
    r = await msg.render_message("m1", make_lead(), camp, None)
    assert any("guideline 300" in w for w in r.warnings)


async def test_render_message_unknown_template():
    with pytest.raises(KeyError):
        await msg.render_message("nope", make_lead(), make_campaign(), None)


async def test_draft_comment_rejects_then_retries_then_gives_up():
    post = make_lead().posts[0]
    good = (
        "Removing the approval step is the part most teams skip. Did support load move elsewhere?"
    )
    text, problems = await msg.draft_comment(
        post, make_lead(), make_campaign(), FakeLLM(["Great post!", good])
    )
    assert text == good and problems == []
    text, problems = await msg.draft_comment(
        post, make_lead(), make_campaign(), FakeLLM(["Great post!", "So true!"])
    )
    assert text is None and problems
    text, problems = await msg.draft_comment(post, make_lead(), make_campaign(), FakeLLM(fail=True))
    assert text is None and "llm error" in problems[0]


def test_campaign_check_default_is_clean_and_flags_problems():
    errors, warnings = msg.campaign_check(make_campaign())
    assert errors == [], errors
    bad = make_campaign(
        messages={
            **make_campaign().messages,
            "m1": "Hi {firstname}",
            "m2": "Same text for everyone.",
        }
    )
    errors, warnings = msg.campaign_check(bad)
    assert any("firstname" in e for e in errors)
    assert any("m2" in w and "varies" in w for w in warnings)
    none_hook = make_campaign(personalization={"hook": "none", "hook_fallback": ""})
    _, warnings = msg.campaign_check(none_hook)
    assert any("{hook}" in w for w in warnings)
    no_name = Campaign.model_validate({**make_campaign().model_dump(), "agent_name": ""})
    errors, _ = msg.campaign_check(no_name)
    assert any("agent_name" in e for e in errors)


def test_campaign_check_accepts_custom_columns():
    c = make_campaign(messages={**make_campaign().messages, "m2": "Re {pain_point} at {company}."})
    errors, _ = msg.campaign_check(c)
    assert errors  # a bare column name is unknown until the CSV declares it
    errors, _ = msg.campaign_check(c, {"pain_point"})
    assert errors == []


def test_campaign_check_custom_prefix_is_a_warning_without_csv_and_error_with_wrong_csv():
    c = make_campaign(
        messages={**make_campaign().messages, "m2": "Re {custom_pain_point} at {company}."}
    )
    errors, warnings = msg.campaign_check(c)
    assert errors == []
    assert any("custom_pain_point" in w and "pain_point" in w for w in warnings)
    errors, _ = msg.campaign_check(c, {"pain_point"})
    assert errors == []
    errors, _ = msg.campaign_check(c, {"industry"})
    assert errors and "custom_pain_point" in errors[0]


def test_newest_post_prefers_known_age():
    lead = make_lead(
        posts=[
            PostRef(text="old", posted_days_ago=30),
            PostRef(text="unknown"),
            PostRef(text="new", posted_days_ago=1),
        ]
    )
    assert msg.newest_post(lead).text == "new"
    assert msg.newest_post(make_lead(posts=[])) is None
