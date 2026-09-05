# Daily use

Everything you do after the first campaign is running. Most of it is five minutes a day.

## Running

```bash
linkedin-agent run                 # keeps going until Ctrl-C
linkedin-agent run --once          # one scheduling pass, do what is due, exit (for cron)
linkedin-agent run --headless      # no visible browser window
linkedin-agent run --max-tasks 5   # stop after five actions
```

The agent only acts while `run` is up. A closed laptop pauses everything; the next `run`
picks up where it stopped, nothing is lost. For a campaign of hundreds of people, a
small machine that stays on is worth having.

### Leaving it running without a terminal

`run` is tied to the terminal that started it. Closing that window, or quitting the app it
lives in, kills the agent. To detach it:

```bash
nohup caffeinate -is linkedin-agent run --headless > ~/.linkedin-agent/run.log 2>&1 &
```

`caffeinate -is` is macOS and keeps the machine awake for as long as the agent runs, which
matters more than any setting in this file: an asleep laptop is an agent doing nothing. On
Linux drop `caffeinate -is` and use your own sleep settings.

Then `linkedin-agent status` tells you whether it is up and its pid — it reads a heartbeat
file, so it works from any terminal, including one an assistant is using. `kill <pid>`
stops a detached run; there is no `stop` command, and Ctrl-C only reaches a run you can
see. Follow it with `tail -f ~/.linkedin-agent/run.log`.

**If you drive the agent from Claude, Copilot or Cursor, start `run` in your own terminal,
never by asking the assistant to run it for you.** A process an assistant starts is a child
of that session and dies when the session ends, mid-action. The skills tell assistants to
hand you the command instead.

By default the Chrome window the agent drives is visible. Do not close it: the agent
notices and restarts the browser, but it costs a task. `--headless` hides it, or set
`LINKEDIN_AGENT_HEADLESS=true` in `~/.linkedin-agent/.env`.

What the lines mean:

```
09:00  tick      12 tasks scheduled · 2 deferred (caps/spacing)
09:03  visit     Jane Doe        ok
09:05  connect   Bob Smith       sent
09:07  waiting   112s
09:09  message   Ann Lee         failed · other; retry 1/3
09:30  check_connection Bob Smith  rate_limited → 08:00 tomorrow
```

- **tick**: the scheduler looked at every lead and created tasks for the steps that are
  due. "deferred" means a cap or the one-touch-per-day rule held some back until later.
- **waiting**: the human-pacing pause between actions, 45 seconds to 3 minutes.
- **retry n/3**: an action failed; it is retried after 10 minutes, up to three attempts,
  then the lead's step stalls and shows in `status` and on the dashboard.
- **rate_limited**: today's cap for that action is used up. The task waits for tomorrow.

## The dashboard

```bash
linkedin-agent ui
```

Opens `http://127.0.0.1:8765/` and refreshes every 30 seconds. It runs next to `run`,
not instead of it, so use a second terminal. It is bound to your machine only.

- **Top row.** Account health (login, breaker, governor, ramp week), today's usage
  against every cap, leads by stage, the last results.
- **Leads.** Every person with stage, current step, when it is next due, and the
  invited, connected, messaged and replied times. Search, filter by stage or campaign,
  sort by any column. Click one for the full picture: profile, posts seen, the sequence
  history, and every task with the exact text sent.
- **Inbox.** People who replied, with a "mark handled" button.
- **Review.** Drafted comments to approve, edit or reject.
- **Queue** and **Activity.** Tasks and the action log.
- **Report.** Acceptance and reply rates against the benchmarks.
- **Campaigns.** Each file's steps, with pause and resume.

Every button does exactly what the matching command does.

## Or do all of this by talking

Connect the MCP server to Claude Code, Claude Desktop, GitHub Copilot or Cursor and the
whole routine becomes a conversation: "who replied", "why is Bob stalled", "add these
people to mine", "approve that comment". Setup and the rules the assistant follows are
in [Claude, Copilot and Cursor](mcp.md). The run loop stays in your terminal; the
assistant only reads and changes the agent's state.

## Replies

```bash
linkedin-agent inbox
Jane Doe     replied 2026-09-04 19:19   https://www.linkedin.com/in/janedoe/
```

When someone replies, their sequence stops at once and they appear here. You answer
them yourself in LinkedIn; the agent never replies to a human. When you have, mark them:

```bash
linkedin-agent inbox --handled janedoe
```

## Approving comments

Only if you set `review_comments: true` in the campaign. Then each drafted comment waits
for you:

```bash
linkedin-agent review
[1/4] Jane Doe · post 2d ago
     "We cut onboarding time in half by removing the approval step…"
     Draft: Removing the approval step is the part most teams skip. Did the support
            load move somewhere else, or did it actually disappear?
     [a]pprove [e]dit [r]eject [s]kip >
```

With review off, comments post on their own and `linkedin-agent log --comments` shows
what was posted. Messages never need approval: they are your templates.

## Checking in

```bash
linkedin-agent status
account default · logged in 2026-09-04 · breaker ok · governor normal · ramp week 1
today: visit 14/15 · follow 3/4 · like_post 2/8 · comment_post 1/2 · connect 5/5 (week 5/23) · message 3/10 · inmail 0/5
queue: 41 queued · 0 running · 2 awaiting review · 96 done · 1 failed · 3 skipped
review: 2 · inbox: 1
leads: warming 12 · invited 21 · messaging 11 · replied 6
last results:
  09-04 19:19  check_replies    Jane Doe                     replied
```

```bash
linkedin-agent log                 # the last 30 actions
linkedin-agent log --comments      # comments posted, with text
```

## Reports

```bash
linkedin-agent report --since 14d
campaign all · 60 leads · last 14d
warm-up completed   48
invites sent        38   accepted 17 (44.7%) · benchmark 28.5% · median time to accept 2.1 days
withdrawn            0
messaged            17   replied 6 (35.3%) · benchmark 10.4%
stages: warming 12 · invited 21 · messaging 11 · replied 6
account: governor normal · breaker ok
```

`report --csv leads.csv` writes one row per person for a spreadsheet.
`report --campaign mine` limits it to one campaign.

## Fixing a stuck lead

| Situation | Command |
|---|---|
| A step failed three times and stalled | `linkedin-agent retry janedoe` |
| Skip the current step and move on | `linkedin-agent skip janedoe` |
| Put a lead back at any step | `linkedin-agent restart janedoe --step wait.accept` |
| Start a lead over from the beginning | `linkedin-agent restart janedoe` |
| Freeze a whole campaign | `linkedin-agent pause mine` / `linkedin-agent resume mine` |
| Clear a tripped breaker you are sure is a false alarm | `linkedin-agent breaker reset` |

A lead is any of: the slug from their URL (`janedoe`), the full URL, their full name,
or the id. Step ids are the `id:` values in your campaign file; `campaign show mine`
lists them.

## One-off actions

Every action is also a direct command, for a single important person or for testing.
They need no campaign, count against the same caps, and are logged the same way. With
`linkedin-agent run` up, an assistant can queue the same actions through the MCP server.

```bash
linkedin-agent visit    https://www.linkedin.com/in/janedoe
linkedin-agent follow   https://www.linkedin.com/in/janedoe
linkedin-agent like     https://www.linkedin.com/in/janedoe              # newest post, or --post-url
linkedin-agent comment  https://www.linkedin.com/in/janedoe              # drafts, shows, asks; or --text
linkedin-agent connect  https://www.linkedin.com/in/janedoe --note "Enjoyed the onboarding thread."
linkedin-agent message  https://www.linkedin.com/in/janedoe --text "…"
linkedin-agent inmail   https://www.linkedin.com/in/janedoe --subject "…" --text "…"
linkedin-agent check    https://www.linkedin.com/in/janedoe              # connected / pending / not connected
linkedin-agent check    https://www.linkedin.com/in/janedoe --replies    # did they reply?
linkedin-agent withdraw https://www.linkedin.com/in/janedoe
```

## Test a whole sequence in ten minutes

Ask a friend to be the lead. This runs the real playbook with minutes instead of days.
Fast mode drops the send windows, the spacing rule and the pacing pauses, but keeps
every cap, so it is safe on a small list only.

1. Stop any running agent with Ctrl-C.
2. Make a fast copy of the default campaign: `linkedin-agent campaign new fast`, then in
   `~/.linkedin-agent/campaigns/fast.yaml` set every `after` to `1m`, every `window` to
   `any`, the wait step to `params: {repeat_every: 2m, until_days: 1}`, and the reply
   checks to `after: 3m`. Fill in the top block and your messages.
3. `linkedin-agent campaign check fast`
4. A one-line CSV with your friend's URL and first name, then
   `linkedin-agent import friend.csv --campaign fast`.
5. `LINKEDIN_AGENT_FAST_TEST=true linkedin-agent run --headless`

You will see visit, follow, like, comment and connect within about ten minutes, then a
check every two minutes until they accept, then message 1, and messages 2 and 3 at
three-minute gaps unless they reply. A reply ends it and they appear in `inbox`.

Two things to know. On a fresh database the account counts as brand new, so only 15
checks are allowed the first day; after those the checks park until 08:00 tomorrow.
And if you would rather your friend not get all three messages, Ctrl-C after message 1
and `linkedin-agent pause fast`.

## Stopping and removing

- `Ctrl-C` stops the run; the browser closes.
- `linkedin-agent pause mine` freezes a campaign; `resume` continues with delays
  recomputed from today.
- `linkedin-agent logout` deletes the Chrome profile.
- Delete `~/.linkedin-agent/agent.db` to start the lead database from scratch. Your
  login and campaign files are separate and survive.

## Looking inside

Everything is in one SQLite file, `~/.linkedin-agent/agent.db`. Any SQLite client opens
it; reading while the agent runs is safe.

```bash
sqlite3 -header -column ~/.linkedin-agent/agent.db "SELECT first_name, stage, invited_at, connected_at FROM leads"
sqlite3 -header -column ~/.linkedin-agent/agent.db "SELECT action, step_id, status, result FROM tasks ORDER BY created_at DESC LIMIT 10"
```

Tables: `leads`, `lead_sequences` (current step and history), `tasks` (every action with
the raw result from the browser), `action_log` (what counted against the caps),
`review_queue`, `accounts`.
