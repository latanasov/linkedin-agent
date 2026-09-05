from __future__ import annotations

from typing import Any

from ..prompts import JSON_ONLY_RULE, validate_linkedin_url


def build_prompt(profile_url: str, params: dict[str, Any]) -> str:
    profile_url = validate_linkedin_url(profile_url)
    return f"""You are on LinkedIn, already logged in. Withdraw one pending connection request.

1. Navigate to: {profile_url}
2. If the page shows a login form or checkpoint, return {{"status": "failed", "error": "login_required"}}.
3. Look for a "Pending" button in the profile header.
   - If there is no "Pending" button, return {{"status": "not_pending", "error": null}}.
4. Click "Pending". A dialog asks to confirm withdrawing the invitation. Click "Withdraw".
5. When the button changes back to "Connect", return {{"status": "withdrawn", "error": null}}.

Rules:
- Do NOT send a new request. Do NOT click Connect, Follow or Message.
- Do NOT retry more than once.
{JSON_ONLY_RULE}"""
