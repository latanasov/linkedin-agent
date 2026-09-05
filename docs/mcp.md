# Controlling the agent from Claude, Copilot or Cursor

The agent ships an MCP server. Connect it to an AI assistant and you run outreach by
talking: "who replied today", "put these 40 people from Apollo into the fast campaign",
"why is Jane stalled", "draft a campaign for SaaS leads and show me the messages".

```
Claude Code · Claude Desktop · GitHub Copilot (VS Code and CLI) · Cursor
        │  MCP over stdio (started by the client, talks only to it)
        ▼
linkedin-agent mcp          reads and writes ~/.linkedin-agent/agent.db, never opens a browser
        │
        ▼
linkedin-agent run          the loop that actually touches LinkedIn, started by you in a terminal
```

The MCP server never drives the browser. Anything that touches LinkedIn is queued and
executed by `linkedin-agent run` with the usual caps and pacing, so there is never a
second Chrome on your profile and nothing happens while the run loop is off.

## From any project, not only this repository

`skills/linkedin-agent/SKILL.md` is a portable entry point. Installed under
`~/.claude/skills/` (Claude Code) or `~/.copilot/skills/` (Copilot CLI), it makes the
agent clone this repository on first use and follow the setup, outreach or campaign skill
inside it. The install one-liner is in the README.

## Setup

You need the path to the `linkedin-agent` executable inside your virtual environment:

```bash
cd ~/linkedin-agent && source .venv/bin/activate && which linkedin-agent
# /Users/you/linkedin-agent/.venv/bin/linkedin-agent
```

Use that absolute path below. The server reads `~/.linkedin-agent/.env` like every other
command, so no key goes into the client configuration.

### Claude Code

```bash
claude mcp add linkedin-agent -- /Users/you/linkedin-agent/.venv/bin/linkedin-agent mcp
```

Add `--scope user` to make it available in every project. Check with `claude mcp list`.
Inside this repository two skills load automatically: `linkedin-setup` (Claude installs
and configures the agent for a new user, except the key and the LinkedIn sign-in) and
`linkedin-outreach` (the daily workflow and the safety rules).

### Claude Desktop

Settings → Developer → Edit Config, then add to `mcpServers`:

```json
{
  "mcpServers": {
    "linkedin-agent": {
      "command": "/Users/you/linkedin-agent/.venv/bin/linkedin-agent",
      "args": ["mcp"]
    }
  }
}
```

Restart Claude Desktop. The tools appear under the connector icon. Paste the contents of
`.claude/skills/linkedin-outreach/SKILL.md` into a Project's instructions to give it the
workflow.

### GitHub Copilot (VS Code)

Create `.vscode/mcp.json` in any workspace, or add to your user settings under `mcp`:

```json
{
  "servers": {
    "linkedin-agent": {
      "type": "stdio",
      "command": "/Users/you/linkedin-agent/.venv/bin/linkedin-agent",
      "args": ["mcp"]
    }
  }
}
```

Open Copilot Chat in agent mode; the tools are listed under the tools button. Inside this
repository, `.github/copilot-instructions.md` and the skills under `.github/skills/` give
it the workflow; in another workspace, copy them there.

### GitHub Copilot CLI

Copilot's terminal agent reads `~/.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "linkedin-agent": {
      "type": "local",
      "command": "/Users/you/linkedin-agent/.venv/bin/linkedin-agent",
      "args": ["mcp"],
      "tools": ["*"]
    }
  }
}
```

Or, inside a `copilot` session, `/mcp add` and answer the prompts with the same values.
`/mcp` lists what is connected.

Run `copilot` from the repository folder and it picks up two things automatically:
`.github/copilot-instructions.md`, a short version of the rules, and the skills under
`.github/skills/` (the same setup, outreach and campaign skills Claude Code uses; the
folder links to `.claude/skills/`). If your Copilot CLI version does not load skills from
the repository, copy them to `~/.copilot/skills/` or paste the one you need into
`.github/copilot-instructions.md`.

Copilot CLI can also do the whole setup, the same way Claude Code does: say "set up
linkedin-agent for me" from the repository folder.

### Cursor

Settings → MCP → Add new server, or `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "linkedin-agent": {
      "command": "/Users/you/linkedin-agent/.venv/bin/linkedin-agent",
      "args": ["mcp"]
    }
  }
}
```

## The tools

| Tool | What it does |
|---|---|
| `status` | Account health, whether the run loop is active, usage against caps, queue, leads by stage, last results. |
| `report` | Funnel numbers for a window against the benchmarks. |
| `activity`, `tasks`, `task` | The action log; tasks with raw results; one task by id. |
| `list_leads`, `get_lead` | Leads with stage and next step; one lead in full with history and every text sent. |
| `preview_messages` | Every message rendered for one person, nothing sent. |
| `import_leads`, `import_csv` | Add people from rows (an Apollo or Clay result, a pasted list) or a CSV file. |
| `inbox`, `mark_handled` | Who replied; mark one as answered. |
| `retry_lead`, `skip_step`, `restart_lead` | Unstick a lead. |
| `list_campaigns`, `get_campaign`, `check_campaign`, `write_campaign`, `new_campaign` | Read, validate, write and create campaign files. Writes only happen if the file validates. |
| `pause_campaign`, `resume_campaign` | Freeze and unfreeze a campaign. |
| `pending_reviews`, `decide_review` | Approve, edit or reject drafted comments. |
| `reset_breaker` | Clear a tripped circuit breaker. |
| `enqueue_action` | Queue one visit, follow, like, comment, connect, message, InMail, check or withdraw for the run loop. |

## What a session looks like

```
you:    what's the state of things?
claude: (status) Run loop is active since 09:02. 41 leads: 12 warming, 21 invited,
        6 messaging, 2 replied. Invites 5/5 today. Two things need you: 2 replies in
        the inbox and 1 comment waiting for review.
you:    show me the replies
claude: (inbox) Jane Doe replied 2h ago after message 1; Bob Smith 40 min ago after
        message 2. Here are the threads: …  Answer them in LinkedIn and tell me when
        you have.
you:    done with jane
claude: (mark_handled) Jane is marked done.
you:    find 20 VPs of engineering at SaaS companies in Berlin and add them to "mine"
claude: (apollo search, then import_leads) Found 23, 20 with LinkedIn URLs. Imported
        20 (20 new). Their first visit runs today; invites go out Tuesday to Thursday
        mornings Berlin time. Want to see message 1 rendered for one of them?
```

## Rules the assistant follows

These are in the server's instructions and in the skill:

- It never replies to a prospect. Replies are yours.
- Before queueing any action that touches LinkedIn, it tells you exactly what will be
  sent to whom and waits for your yes.
- Campaign messages are your words. It proposes, you approve, and it saves only files
  that pass the checker.
- Fast-test mode and one-off actions are for named test leads, never lists.
- If the run loop is not active, it says so instead of pretending the work happened.

## Troubleshooting

**The client shows the server as failed to start.** Run the command by hand in a
terminal: `/path/to/.venv/bin/linkedin-agent mcp`. It should sit silently waiting for
input (Ctrl-C to quit). Any error printed there is the cause, usually a missing `.env`
or a wrong path.

**Tools work but nothing happens on LinkedIn.** `status` will say the run loop is not
active. Start `linkedin-agent run` in a terminal.

**The assistant ignores the rules.** Make sure the skill or instructions file is loaded
for that client; the server's own instructions are a summary, the skill is the full
playbook.
