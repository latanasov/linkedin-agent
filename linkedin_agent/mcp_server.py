"""MCP server: control the agent from Claude Code, Claude Desktop, GitHub Copilot, Cursor.

Runs over stdio, so it is started by the client and talks only to that client. It opens
the same SQLite database the CLI uses and never opens a browser: LinkedIn actions are
queued for the `linkedin-agent run` loop, which owns the one Chrome on the profile.

Start it with `linkedin-agent mcp`. Client setup is in docs/mcp.md.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import __version__
from .bootstrap import App, build_app
from .config import Settings
from .service import Service, ServiceError, format_import

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
You are operating a LinkedIn outreach agent that runs on the user's own machine.

How it works: the user imports leads into a campaign (a YAML file with their own message
templates and a step sequence). A separate process, `linkedin-agent run`, executes the
steps in a browser with human pacing and safety caps. These tools read and change the
agent's state; they never drive the browser themselves. If `status` shows the run loop is
not active, changes take effect only after the user starts `linkedin-agent run` in a
terminal. A campaign is only needed for sequences: one-off visits, requests and messages
work with no campaign through `enqueue_action` (run loop up) or the CLI commands.

Rules:
- Never reply to a prospect on the user's behalf. Replies show up in `inbox`; the user
  answers them in LinkedIn and then calls `mark_handled`.
- Before `enqueue_action` (visit, follow, like, comment, connect, message, inmail,
  check_connection, check_replies, withdraw), state exactly what will be sent to whom and
  get the user's confirmation in the conversation.
- Messages are the user's words. When writing or editing a campaign, ask for their wording
  or propose it explicitly and let them approve; keep every message varying per person.
- Fast-test mode and one-off actions are for named test leads, not lists.
- `campaign_write` only saves a file that validates. Show the user the errors and
  warnings it returns.
- A lead's `history` and `tasks` (from `get_lead`) explain why it is where it is. Read
  them before suggesting `retry`, `skip` or `restart`.
"""


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def build_server(app: App) -> MCPServer:
    svc = Service(app.deps, app.settings)
    server = MCPServer(
        name="linkedin-agent",
        title="LinkedIn agent",
        version=__version__ or "0",
        instructions=INSTRUCTIONS,
    )

    def guarded(fn: Any) -> Any:
        async def wrapper(*args: Any, **kwargs: Any) -> str:
            try:
                return _text(await fn(*args, **kwargs))
            except ServiceError as e:
                return f"error: {e}"

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        sig = inspect.signature(fn)
        wrapper.__signature__ = sig.replace(return_annotation=str)  # type: ignore[attr-defined]
        wrapper.__annotations__ = {
            **{k: v for k, v in fn.__annotations__.items() if k != "return"},
            "return": str,
        }
        return wrapper

    # ── status ───────────────────────────────────────────────────────

    @server.tool(name="status")
    @guarded
    async def status() -> Any:
        """Account health, whether the run loop is active, today's usage against every cap,
        queue depth, leads by stage, loaded campaigns and the last ten results.
        Call this first in any session."""
        return await svc.status()

    @server.tool(name="report")
    @guarded
    async def report(campaign: str | None = None, since: str = "30d") -> Any:
        """Funnel numbers for a window ('7d', '30d'): invites, acceptance rate, messages,
        reply rate, each against the research benchmark; stages; governor and breaker."""
        return await svc.report(campaign, since)

    @server.tool(name="activity")
    @guarded
    async def activity(limit: int = 50) -> Any:
        """The most recent actions the agent took (what counted against the caps)."""
        return await svc.log(limit)

    @server.tool(name="tasks")
    @guarded
    async def tasks(status: str | None = None, limit: int = 50) -> Any:
        """Tasks with their raw results. status: queued, running, awaiting_review, done,
        failed, skipped; omit for the most recent of any status."""
        return await svc.tasks(status, limit)

    @server.tool(name="task")
    @guarded
    async def task(task_id: str) -> Any:
        """One task by id, including the raw result the browser returned. Use it to see
        what happened to an action you queued with enqueue_action."""
        return await svc.task(task_id)

    # ── leads ────────────────────────────────────────────────────────

    @server.tool(name="list_leads")
    @guarded
    async def list_leads(
        campaign: str | None = None,
        stage: str | None = None,
        search: str | None = None,
        limit: int = 100,
    ) -> Any:
        """Leads with stage, current step, when it is next due, and the invited, connected,
        messaged and replied times. stage: new, warming, invited, connected, messaging,
        replied, nurture, not_accepted, cannot_contact, done, paused. search matches
        name, company, title, headline or URL. A lead with stalled=true needs retry/skip."""
        return await svc.leads(campaign, stage, search, limit)

    @server.tool(name="get_lead")
    @guarded
    async def get_lead(lead: str) -> Any:
        """Everything about one lead: profile as scraped, posts seen, CSV fields, the
        sequence history (every step and its outcome), and every task with the exact text
        sent. lead = slug from their URL ('janedoe'), the URL, the full name, or the id."""
        return await svc.lead(lead)

    @server.tool(name="preview_messages")
    @guarded
    async def preview_messages(lead: str) -> Any:
        """Render every message template of the lead's campaign for that person, exactly
        as it would be sent, hook included. Nothing is sent. Use it after editing a
        campaign or before importing a list, to show the user the real wording."""
        return await svc.preview(lead)

    @server.tool(name="import_leads")
    @guarded
    async def import_leads(campaign: str, rows: list[dict[str, Any]]) -> Any:
        """Add people to a campaign and start their sequences. rows: one dict per person with
        at least linkedin_url; optional first_name, last_name, company, title, location,
        email, timezone; any other key becomes a {custom_<key>} placeholder. Existing
        people are updated, bad URLs and duplicates are skipped and listed. Nothing is sent
        until `linkedin-agent run` picks the steps up; the first step is a profile visit."""
        return format_import(await svc.import_rows(rows, campaign))

    @server.tool(name="import_csv")
    @guarded
    async def import_csv(campaign: str, path: str) -> Any:
        """Import a CSV file from the user's disk (needs a linkedin_url column). Same rules as
        import_leads."""
        return format_import(await svc.import_csv(path, campaign))

    @server.tool(name="inbox")
    @guarded
    async def inbox() -> Any:
        """People who replied. Their sequences are stopped and waiting for the user to
        answer in LinkedIn. Never draft replies to send; the user replies personally."""
        return await svc.inbox()

    @server.tool(name="mark_handled")
    @guarded
    async def mark_handled(lead: str) -> Any:
        """After the user has answered a reply in LinkedIn, mark the lead done so it leaves
        the inbox."""
        return await svc.mark_handled(lead)

    @server.tool(name="retry_lead")
    @guarded
    async def retry_lead(lead: str) -> Any:
        """Re-arm a lead whose current step failed three times and stalled."""
        return await svc.retry(lead)

    @server.tool(name="skip_step")
    @guarded
    async def skip_step(lead: str) -> Any:
        """Skip the lead's current step and move on to the next one."""
        return await svc.skip(lead)

    @server.tool(name="restart_lead")
    @guarded
    async def restart_lead(lead: str, step: str | None = None) -> Any:
        """Put a lead back into its sequence at a step id (see the campaign's steps), or at
        the first step when omitted. Use after a wrong 'cannot_contact' or 'replied'
        verdict, or to re-run someone. Lowers the stage accordingly."""
        return await svc.restart(lead, step)

    # ── campaigns ────────────────────────────────────────────────────

    @server.tool(name="list_campaigns")
    @guarded
    async def list_campaigns() -> Any:
        """Campaign files on disk with mode, step ids, paused flag and leads by stage."""
        return await svc.campaigns()

    @server.tool(name="get_campaign")
    @guarded
    async def get_campaign(name: str) -> Any:
        """A campaign's full YAML text, its parsed steps with routing, and the checker's
        errors and warnings. Built-ins (default, inmail, cold-minimal) can be read too."""
        return svc.campaign_get(name)

    @server.tool(name="check_campaign")
    @guarded
    async def check_campaign(yaml_text: str) -> Any:
        """Validate campaign YAML without saving it: structure, step ids, routing targets,
        placeholders, template lengths. Returns ok, errors, warnings."""
        return svc.campaign_check_text(yaml_text)

    @server.tool(name="write_campaign")
    @guarded
    async def write_campaign(name: str, yaml_text: str) -> Any:
        """Save a campaign file, only if it validates (same checks as check_campaign) and
        its `name:` matches. Editing a live campaign affects its leads from their next step
        on. Show the user the warnings."""
        return svc.campaign_write(name, yaml_text)

    @server.tool(name="new_campaign")
    @guarded
    async def new_campaign(name: str, template: str = "default") -> Any:
        """Create a campaign file from a built-in template (default, inmail, cold-minimal)
        and return it for editing. Fails if the name already exists."""
        return svc.campaign_new(name, template)

    @server.tool(name="pause_campaign")
    @guarded
    async def pause_campaign(name: str) -> Any:
        """Freeze every lead in a campaign and cancel their queued tasks."""
        return await svc.pause(name)

    @server.tool(name="resume_campaign")
    @guarded
    async def resume_campaign(name: str) -> Any:
        """Unfreeze a paused campaign; delays are recomputed from now."""
        return await svc.resume(name)

    # ── review ───────────────────────────────────────────────────────

    @server.tool(name="pending_reviews")
    @guarded
    async def pending_reviews() -> Any:
        """Model-drafted comments waiting for approval (campaigns with review_comments:
        true), with the post they answer."""
        return await svc.review_pending()

    @server.tool(name="decide_review")
    @guarded
    async def decide_review(task_id: str, approve: bool, text: str | None = None) -> Any:
        """Approve a drafted comment as-is, approve with edited text, or reject it. Only
        after the user has seen the draft and said what to do."""
        return await svc.review_decide(task_id, approve, text)

    # ── account & one-offs ───────────────────────────────────────────

    @server.tool(name="reset_breaker")
    @guarded
    async def reset_breaker() -> Any:
        """Clear a tripped circuit breaker. Only when the user is sure the LinkedIn warning
        or the failures were a false alarm; otherwise wait the 48 hours."""
        return await svc.breaker_reset()

    @server.tool(name="enqueue_action")
    @guarded
    async def enqueue_action(
        action: str, linkedin_url: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Queue one LinkedIn action for the run loop to execute with the usual caps and
        pacing. action: visit, follow, like, comment (params.text), connect (params.note),
        message (params.text), inmail (params.subject, params.text), check_connection,
        check_replies, withdraw. Confirm with the user first; then poll `task` with the
        returned task_id for the result. Requires `linkedin-agent run` to be active."""
        return await svc.enqueue_action(action, linkedin_url, params)

    return server


async def serve_stdio(settings: Settings) -> None:
    """Build the app, run the server over stdio until the client disconnects."""
    # stdout is the protocol channel: everything else goes to stderr.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    app = await build_app(settings)
    try:
        server = build_server(app)
        await server.run_stdio_async()
    finally:
        await app.close()


def main(settings: Settings) -> None:
    asyncio.run(serve_stdio(settings))
