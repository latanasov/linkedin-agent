# linkedin-agent: instructions for Copilot

This repository is a local LinkedIn outreach agent. Read `CLAUDE.md` for the code map and
the non-obvious rules; it applies to every assistant, not only Claude.

Three skills under `.github/skills/` (the same files as `.claude/skills/`) say how to
operate it:

- `linkedin-setup`: install and configure the agent for a new user. The user types the
  API key and signs in to LinkedIn; you do everything else and finish with
  `linkedin-agent doctor`.
- `linkedin-outreach`: the daily workflow through the `linkedin-agent` MCP tools.
- `linkedin-campaign`: how to write and validate a campaign YAML file.

Rules that never bend, whatever the task:

- Never reply to a prospect on the user's behalf. Replies are theirs.
- Confirm in the conversation before anything reaches LinkedIn (`enqueue_action`,
  starting a run, one-off commands).
- Never start `linkedin-agent run` from a tool call. It runs for days and your session
  does not: it would be killed mid-action when the session ends, while the user believes
  their campaign is live. Hand them the command to paste, detached if they want to close
  the terminal:
  `nohup caffeinate -is linkedin-agent run --headless > ~/.linkedin-agent/run.log 2>&1 &`
- Messages are the user's words; propose, let them approve, save only files that pass
  `campaign check`.
- Never put the API key, cookies or passwords in the conversation, a printed file, or a
  commit.
- Never change or work around the caps, the ramp, the governor or the breaker.
