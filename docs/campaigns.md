# Writing a campaign

A campaign is one YAML file: who you are, what you say, and the order and timing of every
touch. It lives in `~/.linkedin-agent/campaigns/<name>.yaml`.

Start from a built-in and edit; do not write one from scratch:

```bash
linkedin-agent campaign new mine                        # the research-backed default
linkedin-agent campaign new mine --template inmail      # Sales Navigator / InMail version
linkedin-agent campaign new mine --template cold-minimal # visit, connect, one message
linkedin-agent campaign new mine --template three-week   # one cohort, finished in 21 days
linkedin-agent campaign check mine                      # validate; do this after every edit
linkedin-agent campaign show mine                       # print the steps in order
```

## A cohort in three weeks

`--template three-week` is the default playbook fitted to a deadline: everyone reaches an
ending about 21 days after import, so you can run cohorts back to back and read the numbers
of one before committing the next. It differs from `default` in five ways, each of which
buys days:

- Its own `invite` (Tue–Fri) and `followup` (Mon–Fri) windows, so no step waits out a
  weekend. The invite keeps the good hours; only the day range widens.
- A four-day warm-up, one touch a day, instead of six.
- `repeat_every: 2d` on the acceptance check, which is also the biggest cost saving.
- Eight days for an acceptance rather than 21.
- Reply gaps of 3, 3 and 3 days instead of 3, 5 and 7 — the real price of the deadline,
  and the first thing to relax if you find you have room.
- An invite that times out is withdrawn and followed by **one InMail**, so a cohort is not
  written off after eight days of silence. That needs Sales Navigator and InMail credits;
  without them the step reports `cannot_message` and the lead ends as `not_accepted`
  exactly as it would have, at the cost of one wasted action.

**Size the cohort to your ramp, not to your list.** The limit is not how many invites you
can send in three weeks. It is how fast the whole cohort can clear the *warm-up* and be
invited, because each lead still needs its acceptance window and three follow-ups
afterwards — the last invite has to go out by about day 9 to finish by day 21.

On a new account the cap that binds is comments, at 2 a day in ramp week 1 (follow is 4,
connect 5), and about 60% of a list posts recently enough to get one:

| Your ramp week | Cohort that finishes in 21 days |
|---|---|
| 1 | about 15 |
| 3 | about 25 |
| 5 and after | about 40 |

`linkedin-agent status` shows the ramp week. A bigger cohort is not lost — it runs long.
Sixty people on a new account finish in about 30 days rather than 21. If you want a larger
first cohort on a fresh account, delete `warm.comment` (that is the bottleneck) and accept
a colder invite, or treat the first cohort as a four-week cycle and move to the three-week
cadence once the ramp is over.

## The top block: who you are

```yaml
name: mine                   # must match the file name
agent_name: "Alex"           # required
company_name: "Northwind"
value_proposition: >
  One or two sentences on what you do and for whom. The model reads this when it writes
  a hook or a comment. It is never sent as-is.
booking_link: "https://cal.com/alex/15min"
default_timezone: "Europe/Sofia"   # used for people whose location is unknown
quiet_threshold_days: 30           # no post newer than this = "quiet" person
review_comments: false             # true = you approve every comment before it posts
```

## The messages: what you say

```yaml
messages:
  connection_note: "Enjoyed your post on {post_topic}, glad to stay in touch."
  connection_note_quiet: ""
  m1: |
    Hi {first_name}, thanks for connecting.
    {hook}
    Quick question: how does your team keep an eye on cloud spend today?
  m2: |
    One thing that helped teams like {company}: seeing which service drives the bill
    before the invoice arrives. Happy to share how if useful.
  m3: |
    If it's worth a 15-minute look, grab a slot here:
    {booking_link}

personalization:
  hook: one_sentence           # or: none
  hook_fallback: "Noticed your work at {company}."
```

### Placeholders

| Placeholder | Comes from | If empty |
|---|---|---|
| `{first_name}` `{last_name}` | your CSV, else the profile | "there" / nothing |
| `{company}` `{title}` | your CSV, else the profile | "your company" / nothing |
| `{headline}` `{location}` | the profile / your CSV | nothing |
| `{post_topic}` | the first sentence of their newest post | "your recent post" |
| `{agent_name}` `{company_name}` `{booking_link}` | the top block | nothing |
| `{custom_<column>}` | any extra column in your CSV | nothing |
| `{hook}` | one model-written sentence about their post or headline | `hook_fallback` |

Three rules the renderer applies:

- **A line that is only a placeholder disappears if it is empty.** So put `{hook}` and
  `{booking_link}` on their own lines. Never write "Here is my link: {booking_link}"
  on one line; you would send "Here is my link:" to someone with no link set.
- **Every message needs something that varies per person.** LinkedIn filters identical
  copy sent to many people, and the agent refuses to send the same text twice in a week.
  `campaign check` warns when a message has no placeholder or `{hook}`.
- **Keep them short.** The guidelines are 150 characters for the note, 300 for message
  1, 250 for message 2, 220 for message 3. Longer is allowed; the check warns.

### What works, from the research

- **Message 1 is a thank-you and one question.** No pitch, no link. Reply rates roughly
  double when the first message asks instead of sells.
- **Message 2 gives one concrete example.** Message 3 is the only place for the link.
- **A note on the invite only when it says something specific.** A generic note does
  worse than no note. The default sends a note mentioning their post to people who post,
  and a blank request to everyone else.

### The hook

`{hook}` is the single model-written sentence. It must be under 120 characters, one
sentence, no question, no link. The model reads the person's newest post, or their
headline if they do not post, and your value proposition. If it cannot write something
specific it uses `hook_fallback`. Set `hook: none` to have pure templates.

Always run `linkedin-agent preview <lead>` after editing. It shows every message exactly
as that person would receive it.

### Comments

Comments are different: the model writes them, from the post it is commenting on. The
agent checks each one before posting: no banned filler ("great post", "thanks for
sharing", "let's connect"), no links, no mention of your company, at most three
sentences. A draft that fails is redrafted once, then the step is skipped. Set
`review_comments: true` and every comment waits for you in `linkedin-agent review`.

## The sequence: when things happen

```yaml
steps:
  - {id: warm.visit,   action: visit,        after: 0d, window: any}
  - {id: warm.follow,  action: follow,       after: 1d, window: engage}
  - {id: warm.like,    action: like_post,    after: 1d, window: engage, branch: posts,
     params: {pick: newest}}
  - {id: warm.comment, action: comment_post, after: 2d, window: engage, branch: posts,
     params: {pick: different_from_liked, max_sentences: 3}}
  - {id: invite.posts, action: connect, after: 2d, window: send, branch: posts,
     params: {note_template: connection_note},
     on_result: {sent: wait.accept, already_pending: wait.accept, already_connected: post.m1}}
  - {id: invite.quiet, action: connect, after: 2d, window: send, branch: quiet,
     params: {note_template: connection_note_quiet},
     on_result: {sent: wait.accept, already_pending: wait.accept, already_connected: post.m1}}
  - {id: wait.accept, action: check_connection, after: 1d, window: any,
     params: {repeat_every: 1d, until_days: 21},
     on_result: {connected: post.m1, pending: wait.accept, not_connected: wait.accept, timeout: withdraw}}
  - {id: withdraw, action: withdraw_invite, after: 0d, window: any,
     on_result: {withdrawn: "end:not_accepted", not_pending: "end:not_accepted"}}
  - {id: post.m1, action: message, after: 0d, window: send, params: {template: m1}}
  - {id: post.r1, action: check_replies, after: 3d, window: any,
     on_result: {replied: "end:replied", none: post.m2, no_thread: post.m2}}
  - {id: post.m2, action: message, after: 0d, window: send, params: {template: m2}}
  - {id: post.r2, action: check_replies, after: 5d, window: any,
     on_result: {replied: "end:replied", none: post.m3, no_thread: post.m3}}
  - {id: post.m3, action: message, after: 0d, window: send, params: {template: m3}}
  - {id: post.r3, action: check_replies, after: 7d, window: any,
     on_result: {replied: "end:replied", none: "end:nurture", no_thread: "end:nurture"}}
```

Most people never change this. When you do, this is what each part means.

### One step

| Field | What it does |
|---|---|
| `id` | A name you reference elsewhere. Keep the `phase.name` style. |
| `action` | See the table below. |
| `after` | Minimum wait after the previous step: `30m`, `6h`, `2d`, `1w`. The agent adds up to 40% extra so nothing looks scheduled. |
| `window` | When it may run, in the person's local time. Three are built in — `send`: Tue to Thu, 08:30 to 11:00 and 14:00 to 16:00; `engage`: weekdays 09:00 to 18:00; `any`: Mon to Sat 08:00 to 20:00 — and you can define your own, below. |
| `branch` | `posts` runs only for people who posted recently, `quiet` only for those who did not. Omit for both. |
| `params` | Per action, see below. |
| `on_result` | Where to go for each outcome. A step id, `"end:<stage>"`, or the same step id to repeat. |

### Your own windows

The three built-in windows encode what the research says about when invites and messages
get accepted, and most campaigns should leave them alone. When your audience does not keep
a Monday-to-Friday office week, add a `windows:` block at the top level and name it from a
step:

```yaml
windows:
  gulf:                                    # Sunday to Thursday
    days: [sun, mon, tue, wed, thu]
    hours: ["09:00-12:00", "16:00-18:00"]
  evening:
    days: [tue, wed, thu]
    hours: ["18:00-21:00"]

steps:
  - {id: post.m1, action: message, after: 0d, window: gulf, params: {template: m1}}
```

- **Days** are `mon` to `sun`, or numbers 0 to 6 with Monday as 0.
- **Hours** are `HH:MM-HH:MM` ranges in the person's local time, as many per day as you
  want. A range must open before it closes; a window that spans midnight is two campaigns'
  worth of trouble and is rejected.
- **Naming a built-in redefines it for this campaign only.** `windows: {send: ...}` changes
  what `send` means here and nowhere else.

`linkedin-agent campaign show <name>` prints the resolved days and hours of every window
the campaign uses — read it after every edit:

```
windows (in each person's local time):
  any            Mon–Sat 08:00-20:00
  gulf           Sun–Thu 09:00-12:00, 16:00-18:00 (this campaign)
```

`campaign check` warns when a window is defined but unused, when it redefines a built-in,
and when it is narrower than two hours a week — a narrow window means tasks whose window
closes before the agent reaches them are expired and rescheduled, so leads crawl.

Widening `send` to the whole week is allowed and will lower your acceptance rate. The caps,
the ramp and the per-person spacing are unaffected by any of this.

### Actions

| Action | Params | Possible outcomes |
|---|---|---|
| `visit` | | `ok` |
| `follow` | | `followed`, `already_following`, `cannot_follow` |
| `like_post` | `pick: newest` or `different_from_liked` | `liked`, `already_liked`, `post_not_found`, `cannot_like` |
| `comment_post` | `pick`, `max_sentences` | `commented`, `already_commented`, `post_not_found`, `cannot_comment` |
| `connect` | `note_template` | `sent`, `already_pending`, `already_connected`, `cannot_connect` |
| `check_connection` | `repeat_every`, `until_days` | `connected`, `pending`, `not_connected`, `no_option` |
| `withdraw_invite` | | `withdrawn`, `not_pending` |
| `message` | `template` | `sent`, `not_connected`, `cannot_message` |
| `inmail` | `template`, `subject_template` | `sent`, `cannot_message` |
| `check_replies` | `repeat_every`, `until_days` | `replied`, `none`, `no_thread` |

### Where a step goes next

1. If the step has `until_days` and the time is up, it goes to `on_result.timeout`.
2. If `on_result` names the outcome, it goes there.
3. `cannot_connect` and `cannot_message` end the lead as `cannot_contact`.
4. Any other success goes to the next step in the file that applies to the person's
   branch. After the last step the lead ends as `done`.
5. A failure stalls the step until you `retry` or `skip` the lead.

The five endings: `end:replied` (the goal), `end:nurture` (three messages, no reply),
`end:not_accepted` (invite timed out), `end:cannot_contact`, `end:done`.

Two things run regardless of what you write: before every message the agent checks the
thread and stops if the person already replied, and a retried message checks the thread
first so a lost confirmation never becomes a duplicate.

## Recipes

**Fewer acceptance checks for a big list.** Checks are the largest cost driver. On the
wait step use `repeat_every: 2d`. Acceptances are noticed a day later at most.

**Invite ignored, try InMail.** If you have InMail credits, replace the withdraw step's
ending so the lead gets one InMail instead of being dropped:

```yaml
  - {id: withdraw, action: withdraw_invite, after: 0d, window: any,
     on_result: {withdrawn: fallback.inmail, not_pending: fallback.inmail}}
  - {id: fallback.inmail, action: inmail, after: 1d, window: send,
     params: {template: inmail_body, subject_template: inmail_subject},
     on_result: {sent: "end:nurture"}}
```

and add `inmail_subject` and `inmail_body` under `messages`.

**A whole run in minutes, for testing.** See [Daily use](daily-use.md#test-a-whole-sequence-in-ten-minutes).

## Mistakes the checker catches, and a few it cannot

`campaign check` stops you on: duplicate step ids, an `on_result` that points to a step
that does not exist, a template a step references that is not under `messages`, an
unknown placeholder, an unknown `end:` stage, a bad duration.

It cannot catch these, so watch for them:

- **Quote endings inside `{...}`:** write `"end:replied"`, never bare `end:replied`.
- **`|` versus `>`:** `|` keeps your line breaks (use it for messages), `>` folds them
  into one line (fine for `value_proposition`).
- **Misspelled top-level keys are ignored silently.** `personalisation:` does nothing.
- **Renaming a step id** that people are currently sitting on. Add the new step, then
  `linkedin-agent restart <lead> --step <new-id>` for each of them.
- **`name:` must match the file name**, or `import --campaign` cannot find it.

Editing a campaign affects the people already in it from their next step onward.
