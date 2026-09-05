---
name: linkedin-agent
description: Set up or operate the linkedin-agent LinkedIn outreach tool (github.com/latanasov/linkedin-agent) from any project. Use when the user mentions linkedin-agent, LinkedIn outreach automation, LinkedIn campaigns, connection requests or LinkedIn messaging sequences. Clones the repository if needed and hands over to the detailed skills inside it.
---

# linkedin-agent, from anywhere

This is the entry point. The detailed instructions live in the repository itself, so
the first job is to make sure it is on this machine, then follow the skill that matches
the request.

## 1. Get the repository

```bash
test -d ~/linkedin-agent || git clone https://github.com/latanasov/linkedin-agent ~/linkedin-agent
cd ~/linkedin-agent && git pull --ff-only
```

The repository is private; the user's git credentials are used. If the clone is refused,
ask the user to run the clone themselves once, then continue.

If the user already has it somewhere else, use that path instead of `~/linkedin-agent`.

## 2. Read the skill for the task, then follow it

| The user wants to | Read |
|---|---|
| install, configure, log in, first campaign, first leads, fix a broken setup | `.claude/skills/linkedin-setup/SKILL.md` |
| see status, replies, stalled leads, import people, approve comments, run a test | `.claude/skills/linkedin-outreach/SKILL.md` (needs the MCP server, step 3) |
| write or change a campaign file, fix `campaign check` errors | `.claude/skills/linkedin-campaign/SKILL.md` |
| understand or change the code | `CLAUDE.md`, then `docs/developers.md` |

Read the whole file before acting. `docs/` holds the user-facing guides if you need
more: getting-started, campaigns, daily-use, safety, troubleshooting, mcp.

## 3. Connect the MCP server for daily operation

The outreach skill uses the agent's MCP tools. Register the server once, using the
executable inside the repository's virtual environment:

```bash
# Claude Code
claude mcp add linkedin-agent --scope user -- ~/linkedin-agent/.venv/bin/linkedin-agent mcp
# Copilot CLI: /mcp add inside a session, or ~/.copilot/mcp-config.json (see docs/mcp.md)
```

Only after `~/linkedin-agent/.venv` exists, which the setup skill creates.

## Rules that apply before you read anything else

- The user types the API key and signs in to LinkedIn. Never ask for keys, cookies or
  passwords in the conversation.
- Never reply to a prospect on the user's behalf.
- Confirm in the conversation before anything reaches LinkedIn: starting
  `linkedin-agent run`, one-off commands, `enqueue_action`.
- Never start `linkedin-agent run` from a tool call. It runs for days; your session does
  not, and it would be killed mid-action when the session ends. Hand the user the command
  to paste, detached if they want to close the terminal:
  `nohup caffeinate -is linkedin-agent run --headless > ~/.linkedin-agent/run.log 2>&1 &`
- Do not change or work around the caps, the ramp, the governor or the breaker.
