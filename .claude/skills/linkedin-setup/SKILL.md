---
name: linkedin-setup
description: Install and configure the local LinkedIn outreach agent for a new user, from an empty machine to a verified login, a checked campaign and imported leads. Use when someone asks to set up, install, configure or troubleshoot the setup of linkedin-agent, or when `linkedin-agent doctor` fails.
---

# Setting up linkedin-agent for someone

You are doing the setup on the user's machine through the shell. Two things are theirs
and only theirs: typing the OpenRouter API key, and signing in to LinkedIn. Everything
else you do, and you check your work with `linkedin-agent doctor` at the end.

Say what you are about to do before each step, in one line. Run the commands from the
repository folder with the virtual environment active.

## 1. Install

```bash
python3 --version                      # must be 3.11 or newer; stop and say so if not
git clone https://github.com/latanasov/linkedin-agent ~/linkedin-agent   # unless already cloned
cd ~/linkedin-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
linkedin-agent version
```

If `linkedin-agent` resolves outside `.venv` (`which linkedin-agent`), a stale global
install is shadowing it: uninstall it with that interpreter's pip and run `rehash`.

## 2. Find Chrome

macOS: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`. Linux: `which
google-chrome chromium chromium-browser`. Windows: `C:\Program Files\Google\Chrome\Application\chrome.exe`.
If none exists, run `playwright install chromium` and leave the path empty. Prefer the
user's Chrome: its fingerprint looks like a person.

## 3. Write the settings, without the key

Ask for the time zone (IANA name) if you do not know it. Then:

```bash
linkedin-agent init --skip-key --timezone Europe/Sofia --tier pro --chrome-path "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
```

Tell the user to open `~/.linkedin-agent/.env` and paste their OpenRouter key after
`LINKEDIN_AGENT_OPENROUTER_API_KEY=`. Do not ask them to paste the key into the chat.
Wait until they say it is done, then confirm with `linkedin-agent doctor` (the key line
must be `ok`).

## 4. Log in to LinkedIn, in two steps

```bash
linkedin-agent login --open
```

A Chrome window opens on LinkedIn. Tell the user: sign in as usual, wait for the feed,
close that Chrome window, and say when done. Then:

```bash
linkedin-agent login --verify
```

It prints `Logged in` or `Still not logged in`. On the second, they closed the window
before the feed appeared: run `--open` again. Never ask for cookies or passwords.

## 5. Ask what they want to do: sequences or one-off actions

A campaign is only needed for automated sequences (warm-up, invite, wait, follow-ups). If
the user just wants to visit profiles, send connection requests or messages to specific
people, skip steps 5 and 6: after `doctor`, one-off commands work with no campaign at all
(`linkedin-agent visit <url>`, `connect <url> --note "…"`, `message <url> --text "…"`), and
so does `enqueue_action` from the MCP tools while `linkedin-agent run` is up.

## 5a. Create the campaign (sequences only)

```bash
linkedin-agent campaign new mine
```

Then follow the `linkedin-campaign` skill: ask who they are, the offer in two sentences,
the booking link, and their own wording for the connection note and three messages.
Edit `~/.linkedin-agent/campaigns/mine.yaml` and run `linkedin-agent campaign check mine`
until it prints OK. Show the user the warnings and let them decide.

## 6. Import leads (sequences only)

Ask for a CSV path, or for a list of LinkedIn URLs with names. A CSV needs a
`linkedin_url` column; other columns are optional. Then:

```bash
linkedin-agent import leads.csv --campaign mine
linkedin-agent preview <one lead slug>
```

Show the user the preview: that is exactly what will be sent. Nothing has been sent yet.

## 7. Verify everything

```bash
linkedin-agent doctor
```

Every line must be `ok`. Fix whatever is not, using the hint after the arrow.

## 8. Hand over

Tell the user, in this order:

1. `linkedin-agent run` in a terminal starts the agent; leave it open. The first week
   runs at a quarter of the caps on purpose.
2. `linkedin-agent ui` in a second terminal opens the dashboard.
3. Replies land in `linkedin-agent inbox`; they answer in LinkedIn and mark them handled.
4. Optional: `claude mcp add linkedin-agent -- ~/linkedin-agent/.venv/bin/linkedin-agent mcp`
   lets them run the daily routine by talking, with the `linkedin-outreach` skill.

Offer the ten-minute test on a friend (docs/daily-use.md) before a real list.

## Do not

- Start `linkedin-agent run` yourself unless asked; it acts on the user's account.
- Put the API key, cookies or passwords in the conversation, a file you print, or a
  commit.
- Change the caps or suggest ways around them.
