# Safety and limits

The agent is built to stop rather than push through. This page lists every limit, why
it exists, and what you can change.

## Caps

The most an account does per day and per week. These are the numbers the established
commercial tools use, and they are ceilings: you can lower them, never raise them.

| Action | Per day | Per week |
|---|---|---|
| Connection requests | 20 | 90 |
| Messages | 40 | |
| InMails | 20 | |
| Profile visits | 60 | |
| Likes | 30 | |
| Comments | 8 | |
| Follows | 15 | |
| Withdrawals | 15 | |
| Acceptance checks | 60 | |
| Reply checks | 60 | |

When a cap is reached the remaining tasks wait until 08:00 the next day in your time
zone. Nothing is dropped. A failed action does not count against the cap.

## The ramp

A new account starts slowly on purpose, because a sudden jump in activity is the
clearest bot signal there is:

| Account age | Share of the caps |
|---|---|
| Week 1 | 25% |
| Week 2 | 40% |
| Week 3 | 60% |
| Week 4 | 80% |
| Week 5 on | 100% |

"Age" is counted from the agent's first action, so a fresh database also starts at
week 1. `status` and the dashboard show the current ramp week.

## The governor

LinkedIn throttles accounts whose invites are ignored. The agent watches your acceptance
rate over invites sent 3 to 21 days ago, once a day:

| Acceptance rate | Effect |
|---|---|
| 35% or more | Normal. |
| 30% to 35% | Stays as it was. |
| 20% to 30% | Invites halved. |
| Under 20% | Invites paused until the rate recovers. |

It needs at least 10 invites in the window before it acts. If you see "governor halved",
look at your targeting and your profile before increasing volume.

## The circuit breaker

The agent trips for 48 hours, and stops scheduling anything, when either happens:

- LinkedIn shows a restriction message: "unusual activity", "temporarily limited",
  "verify your identity", or the browser gets rate-limited.
- Three actions in a row fail for ordinary reasons.

`status` shows the reason. Do nothing on LinkedIn for two days. If you are sure it was
a false alarm, `linkedin-agent breaker reset`.

## Session expiry

If LinkedIn signs you out, the agent detects the login redirect, stops the run, and
tells you to run `linkedin-agent login`. Before believing a model report of "login
required" it loads the feed page itself, so an empty page from a crashed browser tab
does not get mistaken for a logout.

## Sleep, crashes and recovery

The agent is built for a laptop that sleeps, closes and loses Wi-Fi.

- **State is on disk before anything happens.** Every lead's step, task and result is in
  SQLite. A closed lid or a killed process loses nothing; the next `run` continues. A
  task left "running" by a killed process is requeued after 30 minutes.
- **Time is wall-clock.** Due steps, windows and timeouts are timestamps, so after a long
  sleep they are simply overdue. A task whose send window passed while asleep is not sent
  at 3 a.m.; it is re-created for the next window.
- **Waking up is noticed.** When a pause lasts far longer than asked, the loop assumes the
  machine slept: it restarts the browser, whose connection is stale after sleep, and waits
  for LinkedIn to answer before claiming anything, up to ten minutes.
- **Browser failures do not use up a lead's attempts.** A crash, a dead tab or a sleep in
  the middle of an action is retried without counting against the three attempts or the
  breaker. Six such retries on one task and it is marked failed for you to look at.
- **Nothing is sent twice.** A retried message checks the thread for the same first line,
  a retried connect sees "Pending", a retried like sees "already liked", a retried comment
  looks for your existing comment on the post first.

What no code fixes: the agent only acts while the machine is awake. On a laptop that
sleeps most of the day the sequences stretch and the caps are rarely reached. For a real
campaign, an always-on machine, or "prevent sleep while plugged in", matters more than
any setting here.

## Pacing and windows

- **Between actions:** 45 seconds to 3 minutes, randomised.
- **Per person:** at most one touch in 24 hours and two in 48 hours.
- **Send window:** invites and messages go out Tuesday to Thursday, 08:30 to 11:00 and
  14:00 to 16:00, in the person's local time. Engagement runs weekdays 09:00 to 18:00.
  Checks and visits run Monday to Saturday, 08:00 to 20:00.
- **Delays:** every `after` in the campaign gets up to 40% extra so nothing looks
  scheduled.
- **The browser restarts** every 20 tasks to keep memory flat.

Fast-test mode (`LINKEDIN_AGENT_FAST_TEST=true`) removes the pacing, spacing and windows
for testing on one or two people. The caps and the ramp still apply.

## Content safeguards

- The agent refuses to send the same message text twice within seven days.
- Before every message it checks the thread and stops if the person already replied.
- A retried message checks the thread first so a lost confirmation cannot become a
  duplicate.
- A reply that was already in the thread before your message does not count as a new
  reply.
- Comments are checked for banned filler phrases, links, length and your company name
  before posting.
- A "cannot connect" verdict is double-checked read-only before it ends a lead.
- Text from your CSV and campaign is sanitised before it reaches the browser model, so a
  stray instruction in a CSV cell cannot steer the browser.

## What the agent will not do

- Reply to people. Replies are yours.
- Write your messages. It fills your templates; at most one sentence per message is
  model-written, and you can turn that off.
- Find leads. It needs a CSV.
- Run more than one LinkedIn account in one process. One profile, one `run`.
- Exceed the caps because you asked.
- Keep engaging after a sequence ends. Leads in `nurture` stop.

## Settings

All in `~/.linkedin-agent/.env`, each prefixed `LINKEDIN_AGENT_`. `init` writes the
first four.

| Setting | Default | Meaning |
|---|---|---|
| `OPENROUTER_API_KEY` | | Required. |
| `DEFAULT_TIMEZONE` | `UTC` | For people whose location is unknown. |
| `TIER` | `pro` | Upper limits: `free`, `pro`, `ultimate`. |
| `CHROME_PATH` | Playwright Chromium | Path to Chrome. Must not change after login. |
| `LLM_PROVIDER` | `openrouter` | `openrouter` or `ollama` (local models, no key). See [Local models](local-models.md). |
| `BROWSER_LLM_PROVIDER` / `TEXT_LLM_PROVIDER` | `LLM_PROVIDER` | Override the provider for one model, e.g. browser on OpenRouter and text on Ollama. |
| `BROWSER_LLM_MODEL` | `google/gemini-2.5-flash` | The model that reads pages and clicks. An OpenRouter id, or an Ollama tag when the provider is `ollama`. |
| `TEXT_LLM_MODEL` | `google/gemini-2.5-flash` | Writes hooks and comments. |
| `OLLAMA_HOST` / `OLLAMA_TIMEOUT_S` | `http://localhost:11434` / `600` | Where Ollama listens and how long one call may take. |
| `BROWSER_LLM_TIMEOUT_S` / `BROWSER_STEP_TIMEOUT_S` | provider default | Budget for one browser-model call and one whole step. Local models get `OLLAMA_TIMEOUT_S`; hosted ones keep browser-use's 60s / 120s. |
| `HEADLESS` | `false` | Hide the browser window. |
| `DAILY_VISIT_LIMIT` `DAILY_CONNECT_LIMIT` `DAILY_MESSAGE_LIMIT` | cap | Lower a cap for this account. |
| `MIN_DELAY_S` / `MAX_DELAY_S` | `45` / `180` | Pause between actions. |
| `TICK_INTERVAL_S` | `300` | How often the scheduler looks for due steps. |
| `BROWSER_MAX_TASKS` | `20` | Restart the browser every N tasks. |
| `PROXY_URL` | | Route the browser through a proxy, for a residential IP. |
| `ACCOUNT` | `default` | Profile name, for running separate accounts from separate homes. |
| `HOME` | `~/.linkedin-agent` | Where everything lives. |
| `FAST_TEST` | `false` | Testing only. |

## Cost

Each browser action is one model call per step, roughly 10 to 20 steps, each with a
page snapshot of about 40,000 tokens. A lead who accepts and never replies takes about
16 actions; one who never accepts takes about 27 because of the daily checks. Call it 22
on average.

| Browser model | Price per million tokens in / out | 300 leads |
|---|---|---|
| Gemini 2.5 Flash (default) | $0.30 / $2.50 | about $105 |
| Claude Haiku 4.5 | $1 / $5 | about $315 |
| Gemini 2.5 Pro | $1.25 / $10 | about $450 |
| Claude Sonnet 5 | $2 / $10 | about $630 |
| Claude Opus 5 | $5 / $25 | about $1,550 |

The cheapest lever is not the model: `repeat_every: 2d` on the acceptance check halves
the cost of people who never accept. If Flash misreads pages, try Sonnet or Gemini Pro
on a single `check` or `visit` before switching a campaign.

## The honest risk

LinkedIn's terms do not allow automation, and detection is what gates everything above.
The caps and the ramp are what the mainstream tools use, but a data-centre IP, a
headless browser and a brand-new account are each a signal. Run from your normal
connection, on a profile with history, and start small.
