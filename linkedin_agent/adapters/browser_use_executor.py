"""TaskExecutor that runs the prompt for an action through browser-use."""

from __future__ import annotations

from typing import Any

from ..core.prompts import run_linkedin_agent, with_deadline
from ..core.tasks import build_prompt
from ..models import Action, Task, TaskResult

# Steps browser-use may spend per action. The note path of a connect is the longest
# flow we have (header, More menu, Connect, the note dialog, type, verify, Send, verify
# Pending), and 12 ran out on a real profile twice in a row.
MAX_STEPS: dict[Action, int] = {
    Action.VISIT: 12,
    Action.CHECK_CONNECTION: 6,
    Action.CHECK_REPLIES: 8,
    Action.FOLLOW: 8,
    Action.LIKE_POST: 8,
    Action.COMMENT_POST: 12,
    Action.CONNECT: 20,
    Action.WITHDRAW_INVITE: 8,
    Action.MESSAGE: 16,
    Action.INMAIL: 16,
}


# Consecutive step errors browser-use tolerates before it abandons the run (its default
# is 2). A connect opens a dialog, and the moment it animates in, every element index the
# model just read goes stale: one bad click, one re-read, and it must succeed at once or
# the run dies with "Element index N not available". Seen live on two profiles in one
# afternoon. The extra room is for that re-read, not for flailing: the step cap still holds.
MAX_FAILURES: dict[Action, int] = {
    Action.CONNECT: 4,
    Action.MESSAGE: 4,
    Action.INMAIL: 4,
}


# browser-use's own per-step timeout, applied when the settings leave it unset.
DEFAULT_STEP_TIMEOUT_S = 120
# The most a task may hold the browser on a hosted model. The run loop executes tasks one
# at a time with no other bound, so a hang inside browser-use (seen live: a message task
# "running" for two hours, the event bus deadlocked on a typing event) held the only
# browser and every task behind it. A local model may legitimately need longer per step;
# when the settings raise the step timeout the budget scales with it instead.
WALL_CLOCK_CAP_S = 15 * 60


def wall_clock_budget_s(max_steps: int, step_timeout_s: int | None) -> float:
    if step_timeout_s is not None:
        return max_steps * step_timeout_s + 60
    return min(max_steps * DEFAULT_STEP_TIMEOUT_S, WALL_CLOCK_CAP_S)


class BrowserUseExecutor:
    def __init__(
        self, llm: Any, *, llm_timeout_s: int | None = None, step_timeout_s: int | None = None
    ) -> None:
        self._llm = llm
        self._llm_timeout_s = llm_timeout_s
        self._step_timeout_s = step_timeout_s

    async def execute(self, task: Task, browser: Any) -> TaskResult:
        prompt = build_prompt(task.action, task.profile_url, task.params)
        max_steps = MAX_STEPS.get(task.action, 10)
        budget = wall_clock_budget_s(max_steps, self._step_timeout_s)
        raw = await with_deadline(
            run_linkedin_agent(
                prompt,
                browser,
                self._llm,
                max_steps=max_steps,
                max_failures=MAX_FAILURES.get(task.action, 2),
                llm_timeout_s=self._llm_timeout_s,
                step_timeout_s=self._step_timeout_s,
            ),
            budget,
            f"{task.action.value} (browser presumed hung)",
        )
        return TaskResult.from_raw(raw)
