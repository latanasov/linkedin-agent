from __future__ import annotations

from typing import Any

from ..prompts import JSON_ONLY_RULE, MISSING_PROFILE_RULE, validate_linkedin_url


def build_prompt(profile_url: str, params: dict[str, Any]) -> str:
    profile_url = validate_linkedin_url(profile_url)
    return f"""You are on LinkedIn, already logged in. Withdraw one pending connection request.

1. Navigate to: {profile_url}
2. If the page shows a login form or checkpoint, return {{"status": "failed", "error": "login_required"}}.
   {MISSING_PROFILE_RULE}
3. Look for "Pending" in the profile header AND inside the "More" menu next to it, which
   is where LinkedIn often puts it. Look in both before deciding: a "Withdraw" entry in
   "More" is the same thing.
   - Only if neither the header nor "More" offers "Pending" or "Withdraw", return
     {{"status": "not_pending", "error": null}}.
4. Click "Pending" (or "Withdraw"). A dialog asks to confirm withdrawing the invitation.
   Click "Withdraw".
5. When the button changes back to "Connect", return {{"status": "withdrawn", "error": null}}.

Rules:
- Do NOT send a new request. Do NOT click Connect, Follow or Message.
- "not_pending" ends the invitation and sends an InMail instead, so never answer it
  without having opened "More" and looked.
- Do NOT retry more than once.
{JSON_ONLY_RULE}"""
