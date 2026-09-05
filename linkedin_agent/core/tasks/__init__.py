"""Prompt builders for every browser action.

Each builder is a pure function (profile_url, params) -> prompt string, so the prompts
are unit-testable without a browser. The executor runs them through browser-use.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...models import Action
from .check_connection import build_prompt as check_connection
from .check_replies import build_prompt as check_replies
from .comment_post import build_prompt as comment_post
from .follow import build_prompt as follow
from .like_post import build_prompt as like_post
from .send_connection import build_prompt as send_connection
from .send_inmail import build_prompt as send_inmail
from .send_message import build_prompt as send_message
from .visit_profile import build_prompt as visit_profile
from .withdraw_invite import build_prompt as withdraw_invite

PROMPT_BUILDERS: dict[Action, Callable[[str, dict[str, Any]], str]] = {
    Action.VISIT: visit_profile,
    Action.FOLLOW: follow,
    Action.LIKE_POST: like_post,
    Action.COMMENT_POST: comment_post,
    Action.CONNECT: send_connection,
    Action.CHECK_CONNECTION: check_connection,
    Action.WITHDRAW_INVITE: withdraw_invite,
    Action.MESSAGE: send_message,
    Action.INMAIL: send_inmail,
    Action.CHECK_REPLIES: check_replies,
}


def build_prompt(action: Action, profile_url: str, params: dict[str, Any]) -> str:
    return PROMPT_BUILDERS[action](profile_url, params)
