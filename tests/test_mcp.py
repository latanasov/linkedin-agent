"""The MCP server exposes every tool with a usable schema, and tool calls reach the service."""

from __future__ import annotations

import json

from linkedin_agent.bootstrap import App
from linkedin_agent.mcp_server import INSTRUCTIONS, build_server

EXPECTED_TOOLS = {
    "status",
    "report",
    "activity",
    "tasks",
    "task",
    "list_leads",
    "get_lead",
    "preview_messages",
    "import_leads",
    "import_csv",
    "inbox",
    "mark_handled",
    "retry_lead",
    "skip_step",
    "restart_lead",
    "list_campaigns",
    "get_campaign",
    "check_campaign",
    "write_campaign",
    "new_campaign",
    "pause_campaign",
    "resume_campaign",
    "pending_reviews",
    "decide_review",
    "reset_breaker",
    "enqueue_action",
}


def _content_text(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


async def test_server_lists_every_tool_with_schema(deps, db, settings):
    server = build_server(App(settings=settings, db=db, deps=deps))
    tools = {t.name: t for t in await server.list_tools()}
    assert set(tools) == EXPECTED_TOOLS
    for name, t in tools.items():
        assert t.description, name
    props = tools["import_leads"].input_schema["properties"]
    assert set(props) == {"campaign", "rows"} and tools["import_leads"].input_schema[
        "required"
    ] == [
        "campaign",
        "rows",
    ]
    assert "linkedin_url" in tools["enqueue_action"].input_schema["properties"]
    assert set(tools["list_leads"].input_schema["properties"]) == {
        "campaign",
        "stage",
        "search",
        "limit",
    }
    assert "Never reply" in INSTRUCTIONS


async def test_tool_calls_round_trip(deps, db, settings):
    server = build_server(App(settings=settings, db=db, deps=deps))
    r = await server.call_tool(
        "import_leads",
        {
            "campaign": "test",
            "rows": [{"linkedin_url": "https://www.linkedin.com/in/janedoe", "first_name": "Jane"}],
        },
    )
    assert "Imported 1 leads (1 new" in _content_text(r)
    r = await server.call_tool("list_leads", {})
    rows = json.loads(_content_text(r))
    assert rows[0]["name"] == "Jane" and rows[0]["step_id"] == "warm.visit"
    r = await server.call_tool("status", {})
    assert '"active": false' in _content_text(r)
    r = await server.call_tool("get_lead", {"lead": "nobody"})
    assert _content_text(r).startswith("error: no lead matches")
    r = await server.call_tool(
        "check_campaign",
        {"yaml_text": "name: q\nagent_name: A\nsteps:\n  - {id: a, action: visit}\n"},
    )
    assert json.loads(_content_text(r))["ok"] is True
