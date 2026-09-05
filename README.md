# linkedin-agent

[![ci](https://github.com/latanasov/linkedin-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/latanasov/linkedin-agent/actions/workflows/ci.yml)

A LinkedIn outreach agent that runs on your own computer.

You give it a list of people and the messages you would send them. It warms each person
up (visits, follows, likes and comments on a recent post), sends a connection request,
waits for the acceptance, and then sends your messages one by one, stopping the moment
someone replies. It paces itself like a person, stays under LinkedIn's limits, and stops
on its own when something looks wrong.

Nothing leaves your machine except the model calls that drive the browser.

```
┌──────────┐   ┌────────┐   ┌──────┐   ┌─────────┐   ┌─────────┐   ┌─────────────┐
│  visit   │ → │ follow │ → │ like │ → │ comment │ → │ connect │ → │ wait accept │
└──────────┘   └────────┘   └──────┘   └─────────┘   └─────────┘   └──────┬──────┘
                                                                          ↓
                       ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌──────┐
        you reply ←──  │ message 3 │ ← │ message 2 │ ← │ message 1 │ ← │ okay │
                       └───────────┘   └───────────┘   └───────────┘   └──────┘
                          (each message is skipped the moment they reply)
```

## What you get

- **Your words, not the model's.** Every message is a template you wrote, filled in per
  person. At most one sentence per message is model-written, and you can turn that off.
- **A research-backed sequence.** The default playbook follows what the data says works:
  warm-up before the invite, invites on Tuesday to Thursday mornings in the person's own
  time zone, a question first instead of a pitch, three follow-ups at most.
- **Safety built in.** Daily and weekly caps, a four-week ramp for new accounts, an
  acceptance-rate governor, a circuit breaker on LinkedIn warnings, and one touch per
  person per day.
- **You stay in control.** A local dashboard shows every lead and every action. Replies
  land in an inbox for you to answer yourself. Comments can wait for your approval.
- **Talk to it.** An MCP server lets Claude Code, Claude Desktop, GitHub Copilot (VS
  Code or CLI) or Cursor run the whole thing in conversation: import a list from Apollo, draft a
  campaign, triage replies, unstick a lead. It confirms before anything reaches LinkedIn.
- **Sequences are optional.** Visit a profile, send one request or one message to a
  specific person with a single command, no campaign file needed.
- **One login, then hands off.** You sign in once in a normal Chrome window. The agent
  reuses that profile. No cookies to paste, nothing stored in the cloud.

## Fastest start: one prompt

Paste this into Claude Code (or Copilot CLI, swapping `~/.claude` for `~/.copilot`) in any
folder. It installs the skill, then the agent clones this repository and walks you through
the setup. You type your API key and sign in to LinkedIn; it does the rest.

```
Run: mkdir -p ~/.claude/skills/linkedin-agent && gh api repos/latanasov/linkedin-agent/contents/skills/linkedin-agent/SKILL.md -H "Accept: application/vnd.github.raw" > ~/.claude/skills/linkedin-agent/SKILL.md — then read ~/.claude/skills/linkedin-agent/SKILL.md and set up linkedin-agent for me.
```

Next time, the skill is already there: just say "set up linkedin-agent" or "who replied
to my LinkedIn outreach".

## Five-minute start

You need Python 3.11 or newer, Google Chrome, and an [OpenRouter](https://openrouter.ai)
API key.

```bash
git clone https://github.com/latanasov/linkedin-agent
cd linkedin-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium

linkedin-agent init        # asks for your key, time zone and Chrome path
linkedin-agent login       # sign in once in the Chrome window that opens
```

Then describe your offer and load a list:

```bash
linkedin-agent campaign new mine        # copies the default playbook; edit the top block + messages
linkedin-agent import leads.csv --campaign mine
linkedin-agent preview janedoe          # see every message rendered for one person
linkedin-agent run                      # leave it running; Ctrl-C to stop
linkedin-agent ui                       # dashboard at http://127.0.0.1:8765/
linkedin-agent mcp                      # MCP server for Claude / Copilot / Cursor (see docs/mcp.md)
```

The full walkthrough is in [docs/getting-started.md](docs/getting-started.md). With
Claude Code, "set up linkedin-agent for me" does all of it except typing your key and
signing in. `linkedin-agent doctor` checks the result.

## Use it from any project with an AI coding agent

The one-prompt start above installs `skills/linkedin-agent/SKILL.md` under
`~/.claude/skills/` (Claude Code) or `~/.copilot/skills/` (Copilot CLI). From then on,
in any folder, "set up linkedin-agent for me" or "who replied to my LinkedIn outreach
today" makes the agent clone this repository if needed and follow the setup, outreach or
campaign skill inside it. `gh` is used for the download because the repository is
private; with a clone already on the machine, copying the file works too.

## Documentation

| Read this | When |
|---|---|
| [Getting started](docs/getting-started.md) | First install, login, first campaign, first run. |
| [Writing a campaign](docs/campaigns.md) | Your messages, placeholders, the sequence, timing. |
| [Daily use](docs/daily-use.md) | Running, the dashboard, inbox, approving comments, reports, one-off actions. |
| [Safety and limits](docs/safety.md) | Caps, the ramp, the governor, the breaker, what the agent refuses to do. |
| [Troubleshooting](docs/troubleshooting.md) | Every message the agent can show you and what to do about it. |
| [Local models with Ollama](docs/local-models.md) | Run the text model, or both models, on your own machine; how to measure whether it is good enough. |
| [Claude, Copilot and Cursor](docs/mcp.md) | Connect the MCP server and run outreach by talking. |
| [For developers](docs/developers.md) | Architecture, how to run the tests, how to add an action. |
| [Design](docs/design.md) and [Research](docs/research.md) | Why it is built this way and the numbers behind the default playbook. |

## Cost

The browser is driven by a vision model through OpenRouter, or through a local Ollama
model if you prefer, see [Local models](docs/local-models.md). With the default model
(`google/gemini-2.5-flash`) a full sequence costs roughly $0.35 per lead, about $105 for
300 people. See [Safety and limits](docs/safety.md#cost) for the breakdown and how to
choose a different model.

## Please know

LinkedIn's terms of service do not allow automation. This tool works the way every
commercial LinkedIn automation tool works, keeps to the same limits they use, and stops
itself at the first sign of a restriction, but the risk to your account is yours. Start
slowly, use a real profile with history, and run it from your normal home connection.
