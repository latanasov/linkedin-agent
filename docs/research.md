# LinkedIn Outreach Cadence — Research and Recommended Playbook (Sept 2026)

What the current tooling vendors, data reports and practitioner guides agree on about
warm-up, connection, messaging cadence and safe limits, distilled into one playbook the
local agent should implement. Sources are listed at the end; numbers are theirs, the
synthesis and the recommendation are ours.

Caveat: the vendor blogs (Expandi, PhantomBuster, Dripify, Botdog, etc.) sell the
tools they describe. Where their data conflicts I say so. Where several independent
sources agree, I treat it as reliable enough to build on.

---

## 1. What the data says

### 1.1 Cold outreach baseline is poor and getting worse

- Expandi's 2026 benchmark (13.2M connection requests, 6.7M messages, 13k accounts,
  May 2025 → Apr 2026): **28.5% average acceptance**, **10.4% message reply rate**,
  connection-note reply rate down from 3.5% to 2.2% in twelve months.
- Belkins (20M attempts): acceptance with and without a note is a wash
  (26.4% vs 26.4%).
- Overloop / goextrovert benchmarks: 10–15% reply is "good", 20%+ strong, 30%+ top tier.

Cold, un-warmed automation with a templated note is therefore a ~25% acceptance,
~10% reply motion. Everything below is about beating that.

### 1.2 Warm-up before the request is the single biggest lever

- Messages sent after a meaningful comment exchange: **52% reply vs 18% cold**
  (study of 200 accounts, via Salesforge / Sliq write-ups).
- Warmed prospects: **70–84% acceptance and ~40% reply** vs 5–10% baseline (Sliq);
  reply rates "high teens to low twenties", 2–3x cold (growleads).
- Up to **20% of warmed prospects DM first** before the sequence reaches them.
- Expandi: inbound-style campaigns (profile visitors, event attendees) reply at
  13.4–14.2% vs 10.3% for all DMs, i.e. familiarity alone adds a third.
- PhantomBuster's "follow-first protocol": show up 2–3 times (likes or short comments)
  over 7–14 days, then request. Reps sending <25 requests/week see >40% acceptance.

Consistent guidance on *how*:

- Engage on content published in the **last 5 days** where possible; anything older
  than ~30 days reads as digging.
- **One touch per day, spread over 2–5 days.** Three interactions in 48 hours "reads as
  monitoring rather than interest"; five touches in eight days is "the cadence most
  associated with automation tools".
- Comment quality is what matters. A good comment is **1–3 sentences, adds something
  the post did not contain** (a data point, a counter-example, a specific question),
  and **never pitches**. "Great post!" / "So true!" is "the hallmark of a bot".
- Follow is a low-risk, notification-generating touch that works for prospects who
  post rarely.
- Endorsing skills appears in Dripify's step list but no source shows it moving
  acceptance or reply; several call it spammy. Skip it.

### 1.3 The connection request

- Note vs no note is genuinely contested: Botdog (16k invites) and ReactIn (80k
  campaigns) find **blank requests accepted far more** (55–68% vs 28–45%); Belkins finds
  no difference; Cleverly finds contextual notes win. The reconciling variable is
  whether the note is *earned*: a templated pitch-note lowers acceptance, a one-line
  reference to a real prior interaction does not hurt and lifts post-acceptance reply
  (5.4% → 9.4% in one dataset).
- So: **blank note when cold, one-line contextual note when a comment preceded it.**
- Timing: **Tue–Thu, 8:30–10:30 or 14:00–16:00 in the recipient's time zone.** One
  400-campaign analysis attributes a 30–45% reply lift purely to moving sends to
  Tuesday 10:00 local. Weekend sends get under half the reply rate.
- Acceptance rate feeds LinkedIn's own throttling: **below ~30% acceptance the
  algorithm tightens your limits**; "I don't know this person" reports hurt most.
- **Withdraw pending invites after 14–21 days.** Stale pendings drag acceptance rate
  and account health. After withdrawing you cannot re-invite the same person for 3
  weeks.

### 1.4 After acceptance: short, question-first, few follow-ups

- First message **within 24 hours** of acceptance, **2–4 lines, under 300 characters**
  (mobile truncates around 120, so the hook goes first). Messages under 400 characters
  reply ~2x / +22% vs longer ones.
- **Do not pitch in message one.** Ask one specific question. A 58% reply rate case came
  from messages that "wanted a conversation, not a calendar booking". Messages that
  reference the prospect's post reply 5–8 points higher than sender-centric ones.
- Follow-ups: Expandi finds the **first follow-up adds nothing (−0.6%) and the second
  adds +4.05%**; practitioners converge on **3 follow-ups max, 3–7 days apart, each
  shorter than the last**, e.g. days 1, 4, 9, 14. More than that "feels like
  harassment and leads to blocks".
- After the sequence, **switch channel or drop to slow nurture** (an occasional like),
  do not keep chasing on LinkedIn.
- Any reply ends the automated sequence; a human takes over.

### 1.5 Safe limits and account behaviour (2026 consensus)

LinkedIn no longer publishes hard caps; vendors describe a "reputation gradient" where
limits scale with acceptance rate and account age. The numbers below are the safe
zone every source lands in.

| Action | Safe daily | Weekly / notes |
|---|---|---|
| Connection requests | 15–25 | **100 per rolling 7 days** (80 without Sales Navigator). Spread across the week, never a burst |
| Messages to connections | 30–50 unique | up to 80–100 if all unique text; identical copy triggers spam filters |
| InMail | plan credits | 30–50/day ceiling |
| Profile views | 50–80 | notifications stop showing beyond ~100 |
| Likes + comments + shares | 50–100 combined | comments judged on content and clustering, keep comments ≤10–15 |
| Follows | 20–30 | mass follow/unfollow flagged quickly |
| Invite withdrawals | 10–20 | keep pending list short |

Behavioural rules that recur in every guide:

- **Ramp new or dormant accounts over 4–6 weeks**: week 1 at ~25% of target (≤10
  invites/day), week 2 ~40%, week 3 ~60%, then +10–20% per week. "Zero to forty
  invites on day three" is the most common cause of restriction.
- **Consistency over volume**: same time windows daily, two shorter sessions beat one
  burst, no weekend spikes.
- **Randomised gaps** between actions, working-hours only.
- **Monitor acceptance rate weekly** and cut invite volume when it drops below 30%.
- A restriction lasts 1–3 weeks; a second one is usually longer or permanent.

---

## 2. Recommended playbook for the local agent

Two entry branches based on what the profile visit finds, then one shared
post-acceptance track. Every day offset is a minimum; the engine adds jitter and only
executes inside the allowed window.

### 2.1 Branch A — prospect posts (a post in the last 30 days)

| Day | Step | Rule |
|---|---|---|
| 0 | `visit` | scrape headline, about, location, last 3 posts (URL, date, first 300 chars), activity recency |
| 1 | `follow` | optional, on by default; skipped if already following |
| 2 | `like_post` | newest post, preferably ≤5 days old |
| 4 | `comment_post` | a *different* post if one exists, else the same one. 1–3 sentences, adds something, no pitch, no link, no "great post". Drafted by the LLM from the post text + campaign context, **reviewed by you** in v1 (see design) |
| 6–7 | `connect` | note = one line referencing the comment ("Enjoyed the thread on X, would be glad to stay in touch"), ≤150 chars. Tue–Thu, morning window, recipient local time |

### 2.2 Branch B — prospect is quiet (no post in 30 days)

| Day | Step | Rule |
|---|---|---|
| 0 | `visit` | as above; also note company page URL |
| 1 | `follow` | |
| 3 | `like_post` | on the company page's latest post *if the prospect is tagged or reshared it*; otherwise skip |
| 4–5 | `connect` | **blank note** (highest acceptance for cold), or ≤100-char role/company reference if the campaign insists |

### 2.3 Waiting for acceptance

- `check_connection` (read-only) daily from day +1, Tue–Sat, for up to **21 days**.
- On `connected` → post-acceptance track starts, first message due **within 24 h**
  but inside the send window.
- On day 21 still pending → `withdraw_invite`, lead → `not_accepted`, sequence ends.
  Optionally hand off to the email track. No re-invite for 21 days after withdrawal.

### 2.4 Post-acceptance track (both branches)

| Offset from acceptance | Step | Content rule |
|---|---|---|
| ≤ 1 day | `message` M1 | thanks + **one specific question** tied to their post/role, ≤300 chars, no pitch, no link |
| +3–4 days | `message` M2 | one insight or resource relevant to the question, 1–2 lines, still no booking link |
| +5–7 days | `message` M3 | soft ask, may include the booking link, ≤2 lines |
| +7 days | `message` M4 (optional, off by default) | "should I close the loop?" one-liner |
| after M3/M4 | `nurture` | sequence ends; agent likes one post every ~30 days for 3 months, then stops |

- **Any reply at any point ends the sequence** (`check_replies` runs before every
  message and once a day for leads with an open thread). The lead is flagged for you.
- Never send two messages with identical text across leads; every message is
  LLM-drafted from the lead's data with a template fallback that varies wording.

### 2.5 InMail variant (Sales Navigator)

InMail skips the connection step but not the warm-up: Branch A/B days 0–4, then
`inmail` on day 5–6 using the comment as the hook, then the same M2/M3 cadence as
replies to the InMail thread. Cold InMail without warm-up is the 18–25% response case
in vendor templates; warmed should do better, but we have no independent data.

### 2.6 Account rules the agent enforces

- **Ramp**: multiplier by account age since first automated action: wk1 0.25,
  wk2 0.4, wk3 0.6, wk4 0.8, then 1.0. Applies to invites and messages; warm-up
  actions ramp with it too but from a higher floor.
- **Caps** (per account, all configurable downward): invites 20/day, 90/rolling-7d;
  messages 40/day; views 60/day; likes 30/day; comments 8/day; follows 15/day;
  withdrawals 15/day; InMail 20/day.
- **Windows**: invites and messages Tue–Thu 08:30–11:00 and 14:00–16:00 recipient
  local; warm-up actions Mon–Fri 09:00–18:00; nothing on weekends by default.
- **Per-prospect spacing**: at most one touch per prospect per day, at most two in any
  48 h, never a like and a comment on the same post.
- **Acceptance-rate governor**: rolling 7-day acceptance below 30% halves the invite
  cap; below 20% pauses invites and tells you. Recomputed daily.
- **Pending hygiene**: withdrawals scheduled automatically at 21 days.

### 2.7 What to measure

Per campaign and per week: warm-up completion rate, acceptance rate (accepted ÷
sent, 7-day and cumulative), time-to-accept median, M1 reply rate, overall reply rate,
booking-link clicks are not observable so track "reply after M3" as the proxy, and
account health (cap utilisation, governor state, restrictions). The benchmarks to beat
are 28.5% acceptance and 10.4% reply.

---

## 3. What this changes versus the current cloud flow

The current pipeline is visit → connect (with a note) → wait → templated DM. Against
the research it:

- skips warm-up entirely (the largest lever);
- always sends a note, and a generic one;
- sends whenever the task is popped, any weekday, any hour, in the server's zone;
- never withdraws pending invites;
- sends one DM with the booking link in it and stops (no question-first, no
  follow-ups, no reply detection);
- has no ramp and no acceptance-rate feedback into the limits.

The design doc is updated to add the missing actions (`follow`, `like_post`,
`comment_post`, `withdraw_invite`, `check_replies`), a sequence engine driven by a
campaign definition, per-recipient send windows, the ramp and the governor.

---

## Sources

Benchmarks and datasets
- [Expandi — LinkedIn Outreach Benchmarks 2026: 13.2M data points](https://expandi.io/blog/linkedin-outreach-benchmarks-2026/)
- [Expandi — State of LinkedIn Outreach H1 2026](https://expandi.io/blog/state-of-li-outreach-h1-2026/)
- [Expandi — LinkedIn acceptance rate benchmarks 2026](https://expandi.io/blog/linkedin-acceptance-rate/)
- [Belkins — B2B LinkedIn outreach benchmarks (2025 study)](https://belkins.io/blog/linkedin-outreach-study)
- [Botdog — 16,492 invitations analysed](https://www.botdog.co/blog-posts/linkedin-acceptance-rates)
- [ReactIn — Connection request with or without note](https://www.reactin.io/blog/linkedin-connection-request-with-or-without-note)
- [Cleverly — LinkedIn benchmarks 2026](https://www.cleverly.co/blog/linkedin-benchmarks)
- [Overloop — LinkedIn outreach benchmarks 2026](https://overloop.com/blog/linkedin-outreach-benchmarks)
- [SmartReach — State of LinkedIn Outreach](https://smartreach.io/reports/state-of-linkedin-outreach/)
- [goextrovert — Acceptance and reply rate benchmarks](https://www.goextrovert.com/blog/linkedin-acceptance-reply-rates)
- [Leadriver — Acceptance rate data from 50,000+ requests](https://www.leadriver.io/blog/linkedin-connection-request-acceptance-rate-data)

Warm-up and cadence
- [Expandi — The 8-step LinkedIn warm-up sequence](https://expandi.io/blog/linkedin-warm-up-sequence/)
- [PhantomBuster — Follow vs connect: the follow-first protocol](https://phantombuster.com/blog/linkedin-automation/linkedin-follow-vs-connect/)
- [PhantomBuster — Social warming workflow](https://phantombuster.com/blog/social-selling/phantombuster-social-warming/)
- [PhantomBuster — 14-day follow-up sequence for silent connections](https://phantombuster.com/blog/social-selling/linkedin-follow-up-sequence/)
- [Sliq — Warm up LinkedIn prospects](https://getsliq.com/agents/warm-up-linkedin-prospects)
- [Sliq — LinkedIn outreach best practices: the sequence that works](https://getsliq.com/blog/linkedin-outreach-best-practices)
- [growleads — Warm up prospects before connecting](https://growleads.io/blog/warm-up-prospects-linkedin-connection-strategy/)
- [Salesforge — LinkedIn commenting strategies that generate pipeline](https://www.salesforge.ai/blog/linkedin-commenting-strategies)
- [Botdog — Building a LinkedIn outreach sequence in 2026](https://www.botdog.co/blog-posts/linkedin-outreach-sequence-2026)
- [Laxis — LinkedIn cold outreach playbook 2026](https://laxis.com/blog/linkedin-cold-outreach-playbook-2026/)
- [Letterdrop — LinkedIn outreach strategy for 2026](https://letterdrop.com/blog/linkedin-outreach-strategy)
- [Dripify — Best practices for optimising campaigns](https://help.dripify.com/en/articles/12821625-best-practices-for-optimising-campaigns-with-dripify)
- [GetReplies — Follow-ups after connection acceptance](https://getreplies.ai/blog/linkedin-follow-ups-after-connection-acceptance-best-practices/)
- [Salesrobot — LinkedIn follow-up templates](https://www.salesrobot.co/blogs/linkedin-follow-up-templates)
- [Cclarity — What 84 warm DMs showed](https://cclarity.io/blog/linkedin-dm-reply-rate)
- [bereach — 3 fixes that beat better copy](https://bereach.ai/sales-workflows/triple-linkedin-message-response-rate)
- [Fuzzy AI — AI LinkedIn outreach guide](https://getfuzzy.ai/blog/ai-linkedin-outreach-guide)
- [SocialNexis — AI-generated messages and response rates](https://socialnexis.com/guides/ai-linkedin-messages-response-rate)
- [Leadspark — Message length guide](https://www.leadspark-ai.com/resources/linkedin-message-length-guide)

Timing
- [PhantomBuster — 4-step timing guide for connection requests](https://phantombuster.com/blog/social-selling/best-time-to-send-linkedin-request/)
- [We-Connect — Best time to send requests and messages](https://we-connect.io/blog/when-is-the-best-time-to-send-linkedin-connection-requests)
- [Konnector — Best time to send LinkedIn messages 2026](https://konnector.ai/best-time-linkedin-messages/)
- [ConnectSafely — Best time to send LinkedIn messages, 2026 data](https://connectsafely.ai/articles/best-time-to-send-linkedin-messages-guide-2026)

Limits, ramp and hygiene
- [PhantomBuster — LinkedIn automation safe limits 2026](https://phantombuster.com/blog/linkedin-automation/linkedin-automation-safe-limits-2026/)
- [PhantomBuster — Account warm-up timeline](https://phantombuster.com/blog/linkedin-automation/linkedin-warm-up-timeline/)
- [PhantomBuster — Connection request limits 2026](https://phantombuster.com/blog/social-selling/linkedin-connection-request-limit/)
- [Expandi — LinkedIn connections limit](https://expandi.io/blog/linkedin-connections-limit/)
- [LeadLoft — LinkedIn limits 2026](https://www.leadloft.com/blog/linkedin-limits)
- [We-Connect — LinkedIn limits 2026](https://we-connect.io/blog/linkedin-limits-2026-complete-guide)
- [Linked Helper — Automation limits 2026](https://www.linkedhelper.com/blog/linkedin-automation-limits)
- [Commentify — LinkedIn comment limits 2026](https://blog.commentify.co/linkedin-comment-frequency-limit/)
- [ReactIn — Warm up a LinkedIn account safely (4-week plan)](https://www.reactin.io/blog/warm-up-linkedin-account)
- [Closely — Warm up a new account for automation](https://blog.closelyhq.com/warm-up-new-linkedin-account-automation-without-getting-restricted/)
- [Salesrobot — Withdraw LinkedIn invitations](https://www.salesrobot.co/blogs/withdraw-linkedin-invitation)
- [Kondo — Should I withdraw unaccepted requests?](https://www.trykondo.com/blog/manage-linkedin-connection-requests)
- [Linked Helper — Pending connections guide](https://www.linkedhelper.com/blog/linkedin-pending-connections)
