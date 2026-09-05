# Running on local models with Ollama

The agent uses two models: a browser model that reads LinkedIn pages and decides what to
click, and a text model that writes one hook sentence per message and the comments. Both
default to Gemini 2.5 Flash through OpenRouter. Both can run locally through Ollama
instead, with nothing about the people you contact leaving your machine and no per-token
cost.

Whether that is practical depends on the browser model. This page says how to switch,
which models are worth trying, and how to measure before you commit a campaign to it.

## Switch

Install [Ollama](https://ollama.com/download), pull the models, and set three lines in
`~/.linkedin-agent/.env`:

```
LINKEDIN_AGENT_LLM_PROVIDER=ollama
LINKEDIN_AGENT_BROWSER_LLM_MODEL=qwen3.5:35b
LINKEDIN_AGENT_TEXT_LLM_MODEL=gemma4:12b
```

The OpenRouter key is not needed in this mode. `LINKEDIN_AGENT_OLLAMA_HOST` changes the
address (default `http://localhost:11434`) and `LINKEDIN_AGENT_OLLAMA_TIMEOUT_S` the
per-call timeout (default 600, because a 40,000-token prompt can take minutes).

Then:

```bash
ollama pull qwen3.5:35b && ollama pull gemma4:12b
linkedin-agent doctor          # checks Ollama answers and both models are pulled
```

Switch back by setting the provider to `openrouter` again. Model names follow the
provider: OpenRouter ids such as `google/gemini-2.5-flash`, Ollama tags such as
`qwen3.5:35b`.

## The two models are different problems

**The text model is easy.** Its prompts are a couple of thousand tokens and its output is
one sentence or three. Any current 9 to 14 billion parameter model handles that in a few
seconds on an M4 Pro. Quality is a little below Flash for comments; the code checks catch
filler, links and length. Run this locally without a second thought.

**The browser model is the hard one.** Every step, the browser library hands the model a
snapshot of the page, around 40,000 tokens, and needs a precise JSON action back. One
LinkedIn action is 10 to 15 steps. So two things decide whether a local model works:

- **Prompt-processing speed.** How fast the machine can read 40,000 tokens. On an M4 Pro,
  a dense 27 billion parameter model reads that in one to two minutes per step; a
  mixture-of-experts model with a few billion active parameters, several times faster.
  That is the difference between 20 minutes per action and 4.
- **Reliability on long pages.** Small models lose track of which button is which on a
  long page and break the JSON contract. Every misread costs a retry, and a run of them
  used to trip the breaker; that is fixed, but a model that misreads often will simply
  stall leads.

The agent's pacing between actions is already minutes, and the daily caps are tens of
actions, so "4 minutes per action" is workable; "20 minutes" is not, and "reads the
header wrong one time in five" is not either.

## Candidates worth trying

Pick models tagged **vision** and **tools** in the Ollama library; the browser library
uses both. Prefer mixture-of-experts variants for the browser model: they read long
prompts much faster for the same quality. Sizes that fit an M4 Pro with 48 GB:

| Role | Try first | Also |
|---|---|---|
| Browser | a Qwen 3.5 or 3.6 model around 35B with a small active-parameter count | Gemma 4 at 26 to 31B; a 30B model tuned for tool use and long tasks |
| Text | Gemma 4 at 12B | Qwen 3.5 at 9B |

Anything tagged **cloud** runs on Ollama's servers, not your machine; it removes the cost
argument and most of the privacy one. Models below 9B are fine for text and not worth
trying for the browser.

## Measure before you decide

Two commands tell you everything. Run them with the local browser model and read the
step log:

```bash
linkedin-agent -v check https://www.linkedin.com/in/<someone-you-know>/
linkedin-agent -v visit https://www.linkedin.com/in/<someone-you-know>/
```

Look for three numbers in the output:

1. **Seconds per step.** The gap between one `📍 Step n` line and the next. Under 30 is
   good, under 60 is workable, above that the campaign will crawl.
2. **Steps per action.** The `check` should finish in 3 to 5 steps, the `visit` in 5 to
   8. A model that needs the whole budget is guessing.
3. **The answer.** `check` must report the true state of that profile (you know it), and
   `visit` must return the real headline and at least one real post with a real post URL.
   Wrong is worse than slow.

Then repeat with Flash for the same two people to compare. If the local model is within
about three times Flash's time and gets both answers right on five profiles in a row, it
is good enough for a fast test on a friend, and after that for a campaign. If not, keep
Flash for the browser and go local for the text model only; the browser model is nearly
all of the bill anyway, so that combination costs almost the same as fully local.

## What would make local models much better

The agent currently asks the browser model to do everything, including reading a button
label. Two changes on the roadmap shrink what the model has to do and would put a local
browser model firmly in the workable range:

- A scripted executor for the deterministic actions: acceptance and reply checks,
  follow, like, withdraw, profile scraping. No model at all for the majority of actions.
- Trimmed page snapshots for the rest: the profile header or the compose panel instead
  of the whole page, 5,000 tokens instead of 40,000.

Neither is needed to run the experiment above.
