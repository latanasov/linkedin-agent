# Getting started

From nothing to a running campaign in about 30 minutes. Every command below is copy-paste.

## 1. What you need

- **Python 3.11 or newer.** Check with `python3 --version`.
- **Google Chrome**, or let the installer fetch a Chromium. Chrome is better: its
  fingerprint looks like a normal person's browser.
- **An OpenRouter API key** from [openrouter.ai/keys](https://openrouter.ai/keys). The
  agent uses a vision model to read LinkedIn pages and a text model to write one
  sentence per message and the comments. Both default to `google/gemini-2.5-flash`.
- **A LinkedIn account with some history.** A brand-new or empty profile is what
  LinkedIn restricts first. The agent ramps a new account up slowly regardless.

## 2. Install

```bash
git clone https://github.com/latanasov/linkedin-agent
cd linkedin-agent
python3 -m venv .venv
source .venv/bin/activate          # on Windows: .venv\Scripts\activate
pip install -e .
playwright install chromium        # skip if you will point init at your own Chrome
linkedin-agent version
```

Always run the agent from a terminal where the virtual environment is active. If a
command says `linkedin-agent: command not found`, run `source .venv/bin/activate` again.

## 3. Initialise

```bash
linkedin-agent init
```

It asks four questions and writes `~/.linkedin-agent/.env`:

| Question | What to answer |
|---|---|
| Your time zone | An IANA name such as `Europe/Sofia` or `America/New_York`. Used when a lead's location is unknown. |
| Cap tier | `pro` unless you know otherwise. It only sets the upper limits. |
| Path to Chrome | On a Mac: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`. Leave empty to use the Playwright Chromium. |
| OpenRouter API key | Paste it. It is stored only in that file. |

You can change any answer later by editing the file. All settings are listed in
[Safety and limits](safety.md#settings).

At any point, `linkedin-agent doctor` checks the whole setup and tells you what is
missing.

## 4. Log in to LinkedIn once

```bash
linkedin-agent login
```

A normal Chrome window opens on the LinkedIn login page. Nothing is attached to it, so
Google sign-in and two-factor prompts work exactly as usual. Sign in, wait until your
feed is showing, close that Chrome window, and press Enter in the terminal. The agent
reopens the profile invisibly to confirm the feed loads and prints:

```
Logged in. Profile saved to ~/.linkedin-agent/profiles/default
```

That profile folder is reused for every action from now on. You will not log in again
unless LinkedIn signs you out.

Two rules:

- **Use the same Chrome for login and for tasks.** Chrome encrypts the profile's cookies
  per browser binary. The path you gave `init` is used for both, so this is automatic
  unless you change it later.
- **Do not open that profile in Chrome yourself** while the agent runs. Two browsers on
  one profile confuse each other.

If you are on a machine with no screen, `linkedin-agent login --cookie <li_at>` seeds the
profile from the cookie value instead. The cookie is used once and discarded.

## Not running sequences? Skip to the end

A campaign is only for automated sequences. If all you want is to visit a few profiles or
send a request or a message to specific people, you are done after the login: the one-off
commands in [Daily use](daily-use.md#one-off-actions) need no campaign, and neither does
asking Claude to queue one for you.

## 5. Describe your offer

```bash
linkedin-agent campaign new mine
```

This copies the default playbook to `~/.linkedin-agent/campaigns/mine.yaml`. Open it in
any editor. You change two parts.

**Who you are:**

```yaml
name: mine
agent_name: "Alex"
company_name: "Northwind"
value_proposition: >
  We help engineering teams cut their cloud bill by a third without touching their
  architecture.
booking_link: "https://cal.com/alex/15min"
default_timezone: "Europe/Sofia"
```

**What you say**, in your own words. The agent never writes a message for you. Each
template has placeholders that are filled in per person:

```yaml
messages:
  connection_note: "Enjoyed your post on {post_topic}, glad to stay in touch."
  connection_note_quiet: ""            # people who do not post get a blank request
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
```

`{hook}` is the one line the model writes: a single sentence about the person's recent
post or headline, under 120 characters. Everything else is yours. The full list of
placeholders and every option is in [Writing a campaign](campaigns.md).

Check the file:

```bash
linkedin-agent campaign check mine
```

It prints `OK` or tells you exactly which line to fix.

## 6. Load your leads

A CSV with one column is enough. More columns make the messages better:

```csv
linkedin_url,first_name,last_name,company,title,location
https://www.linkedin.com/in/janedoe,Jane,Doe,Acme,VP Engineering,"New York, NY"
https://www.linkedin.com/in/bobsmith,Bob,Smith,Contoso,CTO,Berlin
```

```bash
linkedin-agent import leads.csv --campaign mine
```

```
Imported 2 leads (2 new, 0 updated, 0 skipped)
Sequences started: 2
```

Bad URLs are listed and skipped, duplicates are merged, and the location column sets
each person's local working hours. Any extra column, say `pain_point`, becomes a
`{custom_pain_point}` placeholder.

Now look at what one person would actually receive:

```bash
linkedin-agent preview janedoe
```

Every message is printed exactly as it will be sent to Jane, hook included. If the
wording is off, edit the campaign file and preview again. Nothing has been sent yet.

## 7. Run

```bash
linkedin-agent run
```

Leave it running. It prints one line per action and one line every few minutes when it
schedules work:

```
New account: week-1 ramp (25% of caps) is active.
09:00  tick      12 tasks scheduled
09:03  visit     Jane Doe        ok
09:05  visit     Bob Smith       ok
09:07  waiting   112s
```

On a brand-new account the first week runs at a quarter of the normal limits on
purpose. See [Safety and limits](safety.md).

Open the dashboard in a second terminal:

```bash
linkedin-agent ui
```

It opens `http://127.0.0.1:8765/` with every lead, its stage and what happens next.

Ctrl-C stops the run cleanly. Start it again any time; it resumes where it left off.

## 8. What happens over the next weeks

| Day | What the agent does with each lead |
|---|---|
| 0 | Visits the profile, reads the headline and recent posts, works out the time zone. |
| 1 | Follows. |
| 2 | Likes the newest post. |
| 4 | Comments on a different post. The comment is model-written from the post itself. |
| 6 | Sends the connection request, on a Tuesday to Thursday morning in their time zone. |
| 7 to 27 | Checks once a day whether they accepted. Withdraws after 21 days if not. |
| accept +0 | Message 1, a thank-you and one question. |
| +3 | Checks for a reply. None: message 2. |
| +8 | Checks for a reply. None: message 3 with your booking link. |
| +15 | Final check. Still nothing: the lead is parked as `nurture`. |

People who have not posted in 30 days skip the like and comment and get a request
without a note. Anyone who replies at any point stops the sequence and appears in
`linkedin-agent inbox` for you to answer yourself.

## 9. Your five minutes a day

```bash
linkedin-agent inbox                  # who replied; answer them in LinkedIn
linkedin-agent inbox --handled janedoe
linkedin-agent status                 # health, today's usage, queue
linkedin-agent review                 # only if you turned comment approval on
```

Everything in this section is also on the dashboard. The rest of the daily routine is in
[Daily use](daily-use.md).

## Let Claude do the setup

If you use Claude Code, it can run everything above for you except the two things that
are yours: typing the API key and signing in to LinkedIn. Open Claude Code in the
repository folder and say "set up linkedin-agent for me". The `linkedin-setup` skill
walks it through install, settings, the two-step login (`login --open`, you sign in,
`login --verify`), the campaign, the import and a final `doctor` check. It never asks
for your key or password in the chat.

## Want to see it work first?

Ask a friend to be your test lead and run the whole sequence in minutes instead of
weeks. The recipe is in [Daily use](daily-use.md#test-a-whole-sequence-in-ten-minutes).
