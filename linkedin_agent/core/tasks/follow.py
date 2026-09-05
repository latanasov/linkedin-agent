from __future__ import annotations

from typing import Any

from ..prompts import JSON_ONLY_RULE, validate_linkedin_url


def build_prompt(profile_url: str, params: dict[str, Any]) -> str:
    profile_url = validate_linkedin_url(profile_url)
    return f"""You are on LinkedIn, already logged in. Follow one person without connecting.

1. Navigate to: {profile_url}
2. If the page shows a login form or checkpoint, return {{"status": "failed", "error": "login_required"}}.
3. Look for a "Follow" button in the profile header. If it is not there, open the "More"
   menu next to the Connect/Message buttons and look for "Follow" inside it.
4. If you see "Following" (already following), return {{"status": "already_following", "error": null}}.
5. If you find "Follow", click it once. Return {{"status": "followed", "error": null}}.
6. If there is no Follow option anywhere, return {{"status": "cannot_follow", "error": null}}.

Rules:
- Do NOT click Connect, Message, or anything other than Follow / More.
- Do NOT retry more than once.
{JSON_ONLY_RULE}"""
