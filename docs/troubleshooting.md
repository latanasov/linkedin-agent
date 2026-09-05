# Troubleshooting

Every message the agent can show you, what it means, and what to do. Nothing here loses
data: the state is in `~/.linkedin-agent/agent.db` and every command resumes from it.

Start with `linkedin-agent doctor`. It checks Python, the settings file, the key, the
browser, the login, the campaigns, the leads, the breaker and whether the run loop is up,
and prints a fix next to anything that fails.

## Install and setup

**`linkedin-agent: command not found`**
The virtual environment is not active. `source .venv/bin/activate` in the repository
folder, then try again.

**`No module named aiosqlite` (or any other module) when running tests**
A global `pytest` is shadowing the one in the virtual environment. Use
`python -m pytest -q` instead.

**`ERROR: file:///… does not appear to be a Python project`**
You ran `pip install -e .` from the wrong folder. Run it from the repository root, where
`pyproject.toml` is.

**`zsh: bad pattern` or `no matches found`**
A `#` comment or a `{placeholder}` on the command line. zsh treats both specially. Drop
the comment; quote anything with braces.

**`Playwright Chromium is not installed`**
Run `playwright install chromium`, or set `LINKEDIN_AGENT_CHROME_PATH` to your Chrome.
On a Mac that is `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`.

## Login

**The Chrome window opens but Google sign-in does nothing, or a popup never appears**
Make sure you are on the current version: `login` opens a plain, un-automated Chrome
precisely so sign-in popups work. If an older version froze on "Debugger paused", update
and run `login` again.

**`Not logged in after login`**
You closed the window before the feed was showing, or LinkedIn asked for a second
factor you did not complete. Run `login` again and wait for the feed.

**`session expired — run linkedin-agent login; waiting for it`**
LinkedIn signed you out. Run `login` in another terminal and sign in; the run loop stays
up, notices, prints `login detected; resuming` and carries on with a fresh browser.
Nothing is lost. This can happen after a password change or a security check on
LinkedIn's side.

**`stop` says `Stopped.` but the shell prints `terminated`, or a Chrome is left behind**
Versions before 2026-09-05 lost their SIGTERM handler after the first browser task, so
`stop` killed the loop without cleanup. Update, restart the loop once, and check for a
stray Chrome from the old one: `pgrep -fl "user-data-dir=/var/folders" | grep -i chrome`,
then `kill` any pid listed that the new loop did not start.

**`A run loop is already active (pid N, since …)`**
One is running, possibly detached or in another terminal. Only one may run per home:
two would mean two Chromes on one LinkedIn session. `linkedin-agent stop` ends it, then
start yours. If `status` says the process is gone but this still appears, the heartbeat
is a few seconds from expiring; try again.

**`scheduler tick failed: …` or `run loop iteration failed: …`**
Something unexpected happened in one pass — a locked database, a malformed record. The
loop logs it, waits half a minute and continues; nothing is lost. After ten in a row it
stops, on the grounds that the problem needs a person. Run with `-v` for the trace.

**`step 'x' no longer exists in campaign 'mine'`**
A step id was renamed or removed while a lead was sitting on it. The lead is left where
it is and everyone else proceeds. Move it: `linkedin-agent restart <lead> --step <id>`.

**`campaign 'mine' reloaded from mine.yaml` / `not reloaded: …`**
Informational. The loop re-reads edited campaign files within a tick. `not reloaded` means
the file no longer validates; the last good version stays in use until you fix it — run
`campaign check mine` to see why.

## Running

**`0 tasks scheduled` on every tick**
Usually nothing is due yet. Check `status`: if the leads are in `invited`, the agent is
waiting for acceptances; if `nurture`, `replied`, `done` or `not_accepted`, their
sequences have ended. If it says "account gated", the breaker or a session expiry is
holding everything; see below. If a campaign is paused, `resume` it.

**`Rate limit reached for connect today; next at 08:00 tomorrow`**
Normal. Today's cap is used up. See [Safety and limits](safety.md).

**`Ramp week 1`**
Normal for a new account or a fresh database. Caps are at a quarter for seven days.

**`Governor: acceptance rate 24%, invites halved`**
Too few people accept your invites. Improve targeting and your profile before sending
more. Below 20% invites pause until the rate recovers.

**`Circuit breaker tripped for 48h: LinkedIn restriction signal`**
The agent saw "unusual activity" or similar. Do nothing on LinkedIn for two days, then
check your account by hand. `breaker reset` only if you are sure it was a false alarm.

**`Circuit breaker tripped for 48h after 3 failures`**
Three actions in a row failed for ordinary reasons, usually a LinkedIn layout the model
could not read. Look at `log` and the failed tasks on the dashboard. `breaker reset` when
you have seen what failed.

**`failed · other; retry 1/3` with `unknown status '…' from the model`**
The browser model answered with a status the prompt did not offer. A status that merely
extends a known one (`liked_but_url_not_found`) is read as that status; anything else is
retried, and the retry finds the action already done if it was (`already_liked`,
`already_pending`). Nothing to do unless it repeats — then run once with `-v` and share
the step log.

**`failed · other; retry 1/3`**
One action failed and is retried in ten minutes. After three attempts the lead's step
stalls. `retry <lead>` re-arms it, `skip <lead>` moves on without it.

**A lead shows `cannot_contact` but LinkedIn shows the invite as pending**
The model misread the profile. The agent double-checks every such verdict now, so this
should be rare. Put the lead back at the wait step:

```bash
linkedin-agent restart <lead> --step wait.accept
```

**A lead shows `replied` but the person did not answer**
The model counted an older message from a previous conversation. The agent now checks
where the reply sits relative to your message and remembers what was in the thread
before it sent, so this should not recur. Continue with:

```bash
linkedin-agent restart <lead> --step post.r1
```

**`resumed after about N min asleep; checking network`**
The machine slept. The agent restarts the browser and waits for LinkedIn to answer before
continuing. Nothing to do. If it says the network is still unavailable after ten minutes,
it carries on and the next task will fail and retry until Wi-Fi is back.

**`crash (2/6 browser retries); retrying`**
The browser died or the machine slept during an action. These retries do not count
against the lead's attempts or the breaker. After six the task is marked failed; look at
it with `get_lead` or the dashboard and `retry` it.

**`Browser has no usable tab; restarting it`**
The browser window was closed, usually by hand. The agent relaunches it. Run with
`--headless` to keep the window out of sight.

**`Page readiness timeout` and other lines from browser_use**
Noise from the browser library about a blank tab. Harmless. They only show with `-v`.

**`LLM call timed out after 60 seconds` on every step, with a local model**
The browser model cannot answer a 40,000-token page inside the browser library's budget.
The agent now hands local models `LINKEDIN_AGENT_OLLAMA_TIMEOUT_S` (600 by default) for
both the call and the step, so on the current version this means the model itself is too
slow: a step that needs ten minutes is not usable either. Try a smaller,
non-reasoning model, or keep the browser on OpenRouter. See
[Local models](local-models.md).

**`agent stopped after N of M steps without a result`**
The browser model used its whole step budget, or failed too many steps in a row, and
never reported an outcome. The line names the last step error — a timeout means the
browser or the model was slow, and the task is retried as a browser problem without
counting against the lead or the breaker; anything else counts as an ordinary failure
and is retried up to three times. The retry is safe: a connect that did go out reads as
`already_pending`, a message as `already_sent`. If one action hits this repeatedly, run
once with `-v` and share the step log.

**`send_button_not_found`**
The model could not find LinkedIn's Send button. It is retried; the retry checks the
thread first so nothing is sent twice. If it happens repeatedly, run once with
`linkedin-agent -v run` and share the step log.

## Campaigns and messages

**`campaign check` says `unknown placeholder(s)`**
A `{name}` that does not exist. The list is in [Writing a campaign](campaigns.md).
`{custom_x}` needs a column `x` in your CSV.

**`campaign check` warns `nothing varies per person`**
A message with no placeholder. Add `{first_name}`, `{company}` or `{hook}`; LinkedIn
filters identical copy and the agent refuses to send the same text twice in a week.

**`identical_body: same text was sent to another lead in the last 7 days`**
Exactly that. Make the message vary per person.

**`{hook}` always shows the fallback in `preview`**
The text model is not answering. Check `LINKEDIN_AGENT_TEXT_LLM_MODEL` is a model that
exists on OpenRouter and the key has credit. The default is `google/gemini-2.5-flash`.

**The message went out with the first character doubled, or as one long line**
Doubled: the compose field ate a keystroke; the prompt guards against it, tell us if it
recurs. One long line: you wrote the template with `>` instead of `|`.

**Like and comment were skipped**
The person has no post in the last 30 days, so they went down the quiet branch. Check
with `sqlite3 ~/.linkedin-agent/agent.db "SELECT posts FROM leads"`. If they do have
recent posts and `posts` is `[]`, the visit missed them; run `visit <url>` again.

**`skipped: invalid task parameters: Invalid LinkedIn post URL: got 'https://www.linkedin.com/in/…'`**
A visit from an early version stored the profile URL as a post URL. Current versions blank
such a URL when the lead is loaded and like or comment the newest post from the activity
feed instead, so this line should no longer appear. If a lead already skipped past its
like because of it, put it back: `linkedin-agent restart <lead> --step warm.like`.

## Dashboard

**`linkedin-agent ui` prints an address but the page is empty**
The database is empty or the campaigns folder has no files. Import leads first.

**The dashboard shows a lead as invited although they accepted an hour ago**
It shows what the agent knows. The next acceptance check will notice. Checks run once a
day by default.

## Database

**`Error: unable to open database file` with `sqlite3 -readonly`**
The database is in WAL mode, which read-only mode cannot open without its sidecar file.
Drop `-readonly`; reading while the agent runs is safe.

**`no such column`**
The database is upgraded when the agent next opens it. Run any command such as
`linkedin-agent status` once, then try again.

**Start over completely**
Stop the run, then:

```bash
rm ~/.linkedin-agent/agent.db ~/.linkedin-agent/agent.db-wal ~/.linkedin-agent/agent.db-shm
```

Your login profile and campaign files are untouched.
