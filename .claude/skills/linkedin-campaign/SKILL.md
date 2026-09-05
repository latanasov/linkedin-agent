---
name: linkedin-campaign
description: Write, review or debug a campaign YAML for the local LinkedIn agent in this repository. Use when the user wants a new outreach sequence, asks to change delays, messages, branches or routing in a campaign file, or when `linkedin-agent campaign check` reports errors.
---

# Building a campaign file for the local LinkedIn agent

A campaign file is a YAML document that tells the agent who you are, what to say, and
the order and timing of every touch on a lead. It is loaded by `linkedin_agent/campaigns/`
and validated by `Campaign` in `linkedin_agent/models.py` plus `campaign_check` in
`linkedin_agent/core/messages.py`. Everything below mirrors those two, so a file that
follows this document passes `linkedin-agent campaign check`.

The three built-ins in `linkedin_agent/campaigns/` are the reference
implementations. Read `default.yaml` before writing anything from scratch.

## Workflow

1. **Ask five things** if not already known: the sender's name and company, the offer in
   one or two sentences, a booking link if any, whether the account has Sales Navigator
   or InMail credits (decides `standard` vs `inmail`), and how aggressive the user wants
   to be (warm-up or not, how many follow-ups).
2. **Start from a built-in.** `linkedin-agent campaign new <name>` copies `default.yaml`;
   `--template inmail`, `--template cold-minimal` or `--template three-week` copies the
   others. Edit, do not
   rewrite: the step ids and routing in the built-ins are known good.
3. **Write the messages in the user's voice.** The agent never writes a DM. Templates
   are the user's words with placeholders; the model contributes at most the `{hook}`
   line. Ask the user for their wording rather than inventing marketing copy.
4. **Validate**: `linkedin-agent campaign check <name>`. Errors block, warnings are
   judgement calls. Then `linkedin-agent campaign show <name>` to eyeball the step order.
5. **Preview** with a real lead after import: `linkedin-agent preview <lead>` renders
   every message for that person, hook included.
6. **Fast test** on one cooperating lead before a real run (recipe at the end).

## File anatomy

```yaml
name: my-campaign             # must match the file name for lookup; used in import --campaign
agent_name: "Alex"            # required; used in hooks/comments and as {agent_name}
company_name: "Northwind"      # {company_name}; also the model's context for comments
value_proposition: >          # 1–2 sentences; context for hooks and comments, never sent verbatim
  We help engineering teams cut their cloud bill by a third.
booking_link: "https://cal.com/x/15min"   # {booking_link}; a lone {booking_link} line is dropped when empty
default_timezone: "Europe/Sofia"          # used for leads with no location; IANA name
mode: standard                # standard | inmail. Informational: the steps decide the behaviour
quiet_threshold_days: 30      # no post newer than this -> "quiet" branch
withdraw_after_days: 21       # informational only; the real timeout is until_days on the wait step
review_comments: false        # true = every model-written comment waits in `linkedin-agent review`

messages:        # name -> template. Names are free, but see "Message names" below
personalization:
  hook: one_sentence          # one_sentence | none
  hook_fallback: "Noticed your work at {company}."
steps:           # ordered list, see "Steps"
```

Unknown top-level keys are ignored silently, so a typo in a key name is not caught.
Check spelling of `personalization`, `messages`, `steps`.

## Messages

### Placeholders

| Placeholder | Filled from | Default when empty |
|---|---|---|
| `{first_name}` `{last_name}` | CSV, else the scraped profile name | `there` / empty |
| `{company}` `{title}` | CSV, else scraped profile | `your company` / empty |
| `{headline}` `{location}` | scraped profile / CSV | empty |
| `{post_topic}` | first sentence of the newest post, ≤60 chars, hashtags stripped | `your recent post` |
| `{agent_name}` `{company_name}` `{booking_link}` | campaign top block | empty |
| `{custom_<col>}` or `{<col>}` | any extra CSV column, e.g. `custom_pain_point` | empty |
| `{hook}` | one model-written sentence, ≤120 chars, no question, no link | `hook_fallback`, else the line is dropped |

Rules the renderer applies:

- A line that is only a placeholder and renders empty is dropped. So put `{hook}` and
  `{booking_link}` on their own lines; never inline them in a sentence.
- Blank lines collapse to at most one. Trailing spaces are stripped.
- Unknown placeholders are an error in `campaign check`. `{custom_x}` is a warning until
  `import` sees the CSV, then an error if the column is missing.
- `{hook}` with `hook: none` is a warning and the line is dropped every time.

### Message names

Names are free, but a few are special:

- Names starting with `m` (`m1`, `m2`, `m3`, `m4`) are treated as DMs: `campaign check`
  warns if they contain no per-person placeholder, because LinkedIn filters identical
  copy sent to many people. `{agent_name}`, `{company_name}` and `{booking_link}` do
  not count as varying.
- Length guidelines (warnings only, on the raw template): `connection_note` 150,
  `m1` 300, `m2` 250, `m3` 220, `m4` 200, `inmail_subject` 60, `inmail_body` 400.
  Research says invites under 150 and first messages under 300 characters convert best.
- Every template a step references via `template`, `note_template` or
  `subject_template` must exist under `messages`; the validator errors otherwise.
- A blank template is fine only for `connection_note_quiet` (a request without a note).
  A message step whose `template` is blank would send an empty DM; keep DMs non-empty.

### Writing guidance to give the user

- `m1` is a thank-you plus one question, no pitch and no link. Reply rate roughly
  doubles when the first message asks instead of sells.
- `m2` gives one concrete example. `m3` is the only place for `{booking_link}`.
- Comments are model-written from the post. The user does not write them. The model
  is blocked from banned phrases ("great post", "thanks for sharing", "let's connect",
  "our product" …), links, more than 3 sentences, 600 characters, and from naming the
  user's company. Turn on `review_comments` for a nervous user.

## Steps

```yaml
- {id: warm.follow, action: follow, after: 1d, window: engage, branch: any,
   params: {...}, on_result: {...}}
```

| Field | Values | Meaning |
|---|---|---|
| `id` | unique string | Referenced by `on_result`, `restart --step`, and the log. Keep the `phase.name` convention |
| `action` | see table below | What the browser does |
| `after` | `45s`, `30m`, `6h`, `2d`, `1w`, or bare seconds | Minimum delay after the previous step completes. The engine adds 0–40 % jitter |
| `window` | `send`, `engage`, `any`, or one the campaign defines | When the task may run, in the lead's local time. Ignored in fast-test mode |
| `branch` | `any` (default), `posts`, `quiet` | Skip the step when the lead is on the other branch |
| `params` | per action | See table |
| `on_result` | `{status: target}` | Explicit routing. `target` is a step id, `end:<stage>`, or the same step id to repeat |

### Windows

Three are built in:

| Window | Days | Hours (lead's local time) | Use for |
|---|---|---|---|
| `send` | Tue–Thu | 08:30–11:00 and 14:00–16:00 | connect, message, inmail |
| `engage` | Mon–Fri | 09:00–18:00 | follow, like, comment |
| `any` | Mon–Sat | 08:00–20:00 | visit, checks, withdraw |

A campaign may define its own under a top-level `windows:` block, and a step may then
name it. Days are `mon`–`sun` (or 0–6, Monday=0); hours are `HH:MM-HH:MM` ranges, as many
per day as you like:

```yaml
windows:
  gulf:                              # a Sunday-to-Thursday working week
    days: [sun, mon, tue, wed, thu]
    hours: ["09:00-12:00", "16:00-18:00"]
  evening:
    days: [tue, wed, thu]
    hours: ["18:00-21:00"]

steps:
  - {id: post.m1, action: message, after: 0d, window: gulf, params: {template: m1}}
```

Naming a built-in redefines it **for that campaign only**; other campaigns keep the
default. `campaign show` prints the resolved days and hours of every window a campaign
uses, so check there after editing.

The validator rejects a step naming a window that does not exist, a day name it does not
know, hours that are not `HH:MM-HH:MM`, and a range that opens at or after it closes.
`campaign check` warns about a window nothing uses, one that redefines a built-in, and one
narrower than two hours a week.

Suggest a custom window only when the user has a reason: a different market's working week,
or an audience that reads LinkedIn outside office hours. The built-in `send` window encodes
what the research says about acceptance rates — widening it to "all week, all hours" is a
choice to send at times that perform worse, so say so rather than doing it silently.

A task that misses its window by the time it is claimed is skipped as `window_missed`
and re-created at the next window.

### Branches

Decided once, after the visit: `posts` when the newest scraped post is within
`quiet_threshold_days` (or has text but unknown age), else `quiet`. Steps with
`branch: posts` are skipped for quiet leads and vice versa. Both invite steps in
`default.yaml` exist only to send a note to posters and a blank request to quiet leads.
If a like/comment step has no post to act on it is skipped even on the posts branch.

### Actions, params and statuses

| Action | Params | Statuses it can return | Default when not routed |
|---|---|---|---|
| `visit` | – | `ok` | next step; also decides the branch and time zone |
| `follow` | – | `followed`, `already_following`, `cannot_follow` | next step |
| `like_post` | `pick: newest \| different_from_liked` | `liked`, `already_liked`, `post_not_found`, `cannot_like` | next step |
| `comment_post` | `pick`, `max_sentences` (3) | `commented`, `already_commented`, `post_not_found`, `cannot_comment` | next step |
| `connect` | `note_template` (or blank) | `sent`, `already_pending`, `already_connected`, `cannot_connect` | `cannot_connect` → `end:cannot_contact`; others next step |
| `check_connection` | `repeat_every`, `until_days` | `connected`, `pending`, `not_connected`, `no_option` | route explicitly; `no_option` stalls |
| `withdraw_invite` | – | `withdrawn`, `not_pending` | next step |
| `message` | `template`, `allow_identical`, `skip_reply_check` | `sent`, `not_connected`, `cannot_message` | `not_connected`/`cannot_message` → `end:cannot_contact` |
| `inmail` | `template`, `subject_template` | `sent`, `cannot_message` | `cannot_message` → `end:cannot_contact` |
| `check_replies` | `repeat_every`, `until_days` | `replied`, `none`, `no_thread` | route explicitly |

`repeat_every` and `until_days` only make sense with `on_result` routing the step to
itself, plus a `timeout` key for what happens when `until_days` elapses. Without
`timeout` the loop never ends.

### Routing rules, in order

1. If the step has `until_days` and it has elapsed and `on_result.timeout` exists → that.
2. If `on_result` has the returned status → that target.
3. If the status means cannot-contact (see table) → `end:cannot_contact`.
4. If the status is a success or a soft skip → the next step in file order that applies
   to the lead's branch; none left → `end:done`.
5. Anything else is a failure: the step stalls until `linkedin-agent retry <lead>` or
   `skip <lead>`. A failed browser task gets 3 attempts, 10 minutes apart, before
   the step stalls.

End stages: `end:replied`, `end:nurture`, `end:not_accepted`, `end:cannot_contact`,
`end:done`. Any other `end:` value is a validation error. `replied` and `done` leads are
never scheduled again; `nurture` and `not_accepted` can be restarted.

Two safety behaviours run regardless of routing: every sequenced `message` first checks
the thread and ends the sequence as `replied` if the lead answered; and a retried message
checks the thread for the same first line before sending, so a lost confirmation cannot
become a duplicate.

### Things the engine enforces that the file cannot override

Daily and weekly caps (connect 20/day and 90/week, messages 40, InMail 20, visits 60,
likes 30, comments 8, follows 15, checks 60), a four-week ramp on new accounts, the
acceptance-rate governor, one touch per lead per 24 h and two per 48 h, and the
circuit breaker. A campaign that schedules more than that simply runs slower.
Design delays with the caps in mind: a 300-lead campaign at 20 invites a day takes
three weeks to invite everyone.

## Recipes

**Standard playbook** (`default.yaml`): visit → follow +1d → like +1d → comment +2d →
connect +2d (note for posters, blank for quiet) → daily check for 21 days → withdraw on
timeout → m1 on accept → reply check +3d → m2 → reply check +5d → m3 → reply check +7d
→ nurture.

**InMail** (`inmail.yaml`): same warm-up, then `inmail` instead of connect, follow-ups
are InMails too, no wait loop.

**Invite timed out → InMail fallback.** Replace the withdraw step's target:

```yaml
- {id: wait.accept, action: check_connection, after: 1d, window: any,
   params: {repeat_every: 1d, until_days: 14},
   on_result: {connected: post.m1, pending: wait.accept, not_connected: wait.accept, timeout: withdraw}}
- {id: withdraw, action: withdraw_invite, after: 0d, window: any,
   on_result: {withdrawn: fallback.inmail, not_pending: fallback.inmail}}
- {id: fallback.inmail, action: inmail, after: 1d, window: send,
   params: {template: inmail_body, subject_template: inmail_subject},
   on_result: {sent: "end:nurture"}}
```

**No warm-up, one message** (`cold-minimal.yaml`): visit → connect → wait → one DM.
Worst reply rates, fastest to run. Use only for a small, warm list.

**A cohort finished inside three weeks** (`three-week.yaml`): the default playbook fitted
to a deadline. A four-day warm-up, its own `invite` (Tue–Fri) and `followup` (Mon–Fri)
windows so no step waits out a weekend, `repeat_every: 2d` with `until_days: 8` on the
acceptance check, and 3/3/3-day reply gaps. Reach for it when the user says "three weeks"
or wants back-to-back cohorts they can measure.

Size the cohort to the ramp, and say the number out loud rather than letting them import
a list that cannot fit. Invites available in three weeks are the daily connect cap times
three send days (or four with this template's `invite` window), summed over the ramp:
about 75 on a new account, about 180 at week 5. Import no more than two thirds of that —
the last week needs room for messages, not only invites — so 60 is a sensible first
cohort. Whatever they ask for, the arithmetic is the answer, not the ambition.

**Comment approval:** set `review_comments: true`; nothing else changes. Comments park
in `linkedin-agent review`; the sequence waits on them.

**Fewer checks for a large list:** `repeat_every: 2d` on the wait step halves the
biggest cost driver with almost no delay in noticing acceptances.

**Fast end-to-end test** for one cooperating lead: copy the standard steps, set every
`after` to `1m`, the wait step to `repeat_every: 2m, until_days: 1`, reply checks to
`3m`, all windows to `any`, then run with `LINKEDIN_AGENT_FAST_TEST=true linkedin-agent run`.
Fast mode drops windows, spacing and pacing but keeps the caps and the ramp, so a fresh
database allows only 15 checks the first day.

## YAML gotchas seen in practice

- Inside a flow mapping `{...}`, quote targets with a colon: `"end:replied"`, never
  bare `end:replied`.
- `after: 0d` on the first step is required for the visit to run immediately on import.
- Block scalars: `|` keeps line breaks (use for messages), `>` folds them (use for
  `value_proposition`). A message written with `>` becomes one long line.
- Two steps with the same `id`, an `on_result` target that is not a step id, or a
  template name that is not under `messages` are validation errors with the step named.
- `name:` must match the file name (`~/.linkedin-agent/campaigns/<name>.yaml`) or
  `import --campaign <name>` cannot find it.
- Changing a campaign file affects leads already in it from their next step on. Removing
  a step id that leads are currently sitting on makes them error with "unknown step";
  rename by adding the new step and `restart <lead> --step <new>` instead.

## Checklist before handing the file back

- [ ] `linkedin-agent campaign check <name>` prints `OK`; every warning was read.
- [ ] `agent_name`, `company_name`, `value_proposition` filled; `booking_link` set if m3 uses it.
- [ ] Every DM has a per-person placeholder or `{hook}`; `{hook}` and `{booking_link}` sit on their own lines.
- [ ] The wait step routes `connected`, `pending`, `not_connected` and has `timeout` with `until_days`.
- [ ] Both connect steps route `already_connected` straight to the first message.
- [ ] Every reply check routes `replied` to `end:replied`.
- [ ] Windows: `send` on connect/message/inmail, `engage` on warm-up, `any` on checks.
- [ ] `preview <lead>` was run on a real imported lead and the user approved the wording.
