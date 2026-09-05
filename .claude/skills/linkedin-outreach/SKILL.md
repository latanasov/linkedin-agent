---
name: linkedin-outreach
description: Operate the local LinkedIn outreach agent through its MCP tools. Use when the user asks about their LinkedIn campaign state, replies, stalled leads, importing people (from a list, a CSV, Apollo or Clay), writing or changing a campaign, approving comments, or running a test on a named person.
---

# Operating the LinkedIn agent

You control a LinkedIn outreach agent that runs on the user's own machine, through the
`linkedin-agent` MCP tools. The agent visits, follows, likes, comments, connects and
messages people from the user's account, on the user's own message templates, with
human pacing and safety caps. You never touch LinkedIn directly: the tools read and
change the agent's state, and a separate process the user starts in a terminal,
`linkedin-agent run`, executes the work.

## Start every session with `status`

It tells you four things you need before saying anything: whether the run loop is
active, whether the account is gated (session expired, breaker tripped, governor
paused), today's usage against the caps, and what needs the user (inbox, reviews,
stalled leads). Lead with what needs them.

If the run loop is **not active**, say so plainly in your first reply. Imports, restarts
and queued actions are all stored and correct, but nothing happens on LinkedIn until
the user runs `linkedin-agent run` themselves. Do not imply otherwise, and do not start
it for them — see below.

## The run loop has to outlive you

`linkedin-agent run` is a long-lived process: it ticks every few minutes for days, and
its sequences span weeks. Your session is not.

**Never start it from a tool call.** A process you launch through a shell tool is a child
of the assistant session. When the session ends, the terminal closes, or the user quits
Copilot or Claude, the process is killed with it — mid-action, possibly mid-message. The
state survives (everything is in SQLite), but the user believes their campaign is running
and it is not.

Tell the user to start it themselves, in one of these two ways:

```bash
# a terminal they leave open — simplest, and they see the log
linkedin-agent run

# detached: survives closing the terminal and quitting the assistant
nohup caffeinate -is linkedin-agent run --headless > ~/.linkedin-agent/run.log 2>&1 &
```

`caffeinate -is` is macOS and keeps the machine awake while it runs; on Linux use
`nohup linkedin-agent run --headless > ~/.linkedin-agent/run.log 2>&1 &` and the
equivalent sleep setting. `--headless` matters for a detached run: a visible Chrome
window on a machine nobody is watching is just something to close by accident.

Then:

- **Is it up?** `status` (the tool, or `linkedin-agent status`) reports the run loop and
  its pid. It reads a heartbeat file, so it is true across terminals and sessions — you
  can check a loop you never started.
- **Stop it:** `kill <pid>` from that line. There is no `stop` command; Ctrl-C works only
  for a run in a visible terminal.
- **Watch it:** `tail -f ~/.linkedin-agent/run.log` for a detached run.

If a user asks you to start the run loop, give them the command to paste. Do not run it
yourself even when you have a shell: it acts on their LinkedIn account, and it would die
with you.

## Rules that never bend

1. **Replies are the user's.** `inbox` lists people who answered. Show the reply
   context and the profile link, then stop. Do not draft a reply to send, do not queue a
   message to someone in the inbox. After the user has answered in LinkedIn, call
   `mark_handled`.
2. **Confirm before anything reaches LinkedIn.** `enqueue_action` sends real visits,
   requests and messages. Before calling it, state the action, the person and the
   exact text, and wait for the user's yes in the conversation. Never batch it over a
   list.
3. **Messages are the user's words.** When creating or editing a campaign, ask for their
   wording or propose it explicitly and let them approve before `write_campaign`. Keep
   every message varying per person (a placeholder or `{hook}`); the checker warns when
   one does not, and LinkedIn filters identical copy.
4. **Fast-test mode is for one named test lead.** Never suggest it for a list.
5. **Caps are not negotiable.** If the user asks to go faster, explain the ramp and the
   governor (docs/safety.md) instead of looking for a way around them.
6. **Do not reset the breaker to "see if it works".** A trip means LinkedIn showed a
   warning or three actions failed in a row. Read `activity` and the failed `tasks`
   first; reset only when the user has looked and says it was a false alarm.

## Daily triage, in this order

1. `status`: gates first (session expired → tell the user to run `linkedin-agent login`;
   breaker → show the reason and the time it lifts).
2. `inbox`: replies to answer.
3. `pending_reviews`: drafted comments. Show each draft with the post it answers. Approve
   as-is, with the user's edit, or reject, only as the user decides.
4. `list_leads` with `stage` filters for what is stuck: `stalled=true` rows need
   `retry_lead` or `skip_step`; `cannot_contact` and `not_accepted` may deserve a
   `restart_lead` if the user thinks the verdict was wrong. Always `get_lead` first and
   read `history` and `tasks`: the raw `result_error` explains the failure.
5. `report` weekly: acceptance and reply rates against the benchmarks (28.5% and
   10.4%). Below benchmark is a targeting or profile problem before it is a volume one.

## Without a campaign: one-off visits, requests and messages

Campaigns are for automated sequences. For "visit these three people", "send Jane a
connection request with this note" or "message Bob this", no campaign is needed:

1. Confirm the exact action, person and text with the user.
2. `enqueue_action` with the action, the profile URL and the text. It is queued with the
   usual caps and pacing.
3. If `status` says the run loop is not active, tell the user to start `linkedin-agent run`
   (it works with no campaign files) or to run the equivalent CLI command directly:
   `linkedin-agent visit <url>`, `connect <url> --note "…"`, `message <url> --text "…"`.
4. Poll `task` with the returned id for the result.

One person at a time, each confirmed. For a list, that is a campaign.

## Importing people

`import_leads` takes rows: one dict per person with `linkedin_url` and optionally
`first_name`, `last_name`, `company`, `title`, `location`, `email`, and any extra key
which becomes a `{custom_<key>}` placeholder. This is the shape of an Apollo or Clay
result, a CSV the user pasted, or a list they typed. `import_csv` takes a path instead.

Before importing a list from a search tool, show the user the count and a sample of
names and titles, and confirm the campaign. After importing, offer `preview_messages`
on one of them so the user sees the real wording. Bad URLs and duplicates are skipped
and listed in the result; relay them.

The first step for every new lead is a profile visit; invites only go out on Tuesday
to Thursday mornings in the person's own time zone. Set expectations accordingly.

## Writing or changing a campaign

Follow the `linkedin-campaign` skill for the file format; it mirrors the validator.
The tool flow is:

1. `get_campaign` on `default` (or `inmail`, `cold-minimal`) to start from a built-in,
   or `new_campaign` to create the file.
2. Ask the user for who they are, the offer, the booking link, and their wording for
   the connection note and three messages. Propose text only when asked, and mark it as
   a proposal.
3. `check_campaign` on the YAML text. Fix errors; show warnings and let the user decide.
4. `write_campaign`. It refuses invalid files.
5. `preview_messages` on an imported lead so the user reads the result.

Editing a live campaign affects its leads from their next step on; say so.

## Testing on a named person

When the user wants to see the agent work end to end on a friend: create a campaign
with minute-scale delays (`after: 1m`, `window: any`, the wait step
`repeat_every: 2m, until_days: 1`), import that one person, and tell the user to run
`LINKEDIN_AGENT_FAST_TEST=true linkedin-agent run --headless`. Watch it with `tasks`
and `get_lead`. Remind them that a fresh database allows only 15 acceptance checks on
day one.

## Explaining what you see

- Stages: `new` → `warming` → `invited` → `connected` → `messaging` → `replied`;
  endings `nurture` (three messages, no reply), `not_accepted` (invite timed out),
  `cannot_contact` (no way to connect), `done`.
- A task's `result_status` is what the browser model reported; `result_error` is why it
  failed. `send_button_not_found`, `page_did_not_load` and the like are browser
  problems, retried automatically; `restricted` trips the breaker.
- `history` on a lead is the sequence's own log: each step and its outcome, including
  `restarted`, `skipped`, `replied_before_send`.
- Usage rows are today against the cap after the ramp and governor; a full bar means
  that action waits until 08:00 tomorrow in the user's time zone.

## What to say when asked for more

- More volume: the caps, the ramp (25% in week one to 100% in week five) and the
  governor exist because LinkedIn restricts accounts that ignore them. Offer better
  targeting and a second account on a second profile instead.
- Automatic replies: no. Offer to summarise the reply and suggest talking points the
  user can type themselves.
- Finding leads: use the user's connected search tools (Apollo, Clay) and hand the rows
  to `import_leads`; the agent does not search LinkedIn itself.
