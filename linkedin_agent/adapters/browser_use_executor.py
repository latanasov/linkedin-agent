"""TaskExecutor that runs the prompt for an action through browser-use."""

from __future__ import annotations

from typing import Any

from ..core.prompts import run_linkedin_agent
from ..core.tasks import build_prompt
from ..models import Action, Task, TaskResult

MAX_STEPS: dict[Action, int] = {
    Action.VISIT: 8,
    Action.CHECK_CONNECTION: 6,
    Action.CHECK_REPLIES: 8,
    Action.FOLLOW: 8,
    Action.LIKE_POST: 8,
    Action.COMMENT_POST: 12,
    Action.CONNECT: 12,
    Action.WITHDRAW_INVITE: 8,
    Action.MESSAGE: 16,
    Action.INMAIL: 16,
}


class BrowserUseExecutor:
    def __init__(self, llm: Any) -> None:
        self._llm = llm

    async def execute(self, task: Task, browser: Any) -> TaskResult:
        prompt = build_prompt(task.action, task.profile_url, task.params)
        raw = await run_linkedin_agent(
            prompt, browser, self._llm, max_steps=MAX_STEPS.get(task.action, 10)
        )
        return TaskResult.from_raw(raw)
