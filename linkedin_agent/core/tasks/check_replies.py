from __future__ import annotations

from typing import Any

from ..prompts import JSON_ONLY_RULE, sanitize_user_text, validate_linkedin_url


def build_prompt(profile_url: str, params: dict[str, Any]) -> str:
    profile_url = validate_linkedin_url(profile_url)
    snippet = sanitize_user_text(
        str(params.get("last_message_snippet") or ""), max_length=120
    ).strip()
    probe = sanitize_user_text(str(params.get("probe_text") or ""), max_length=120).strip()
    if probe:
        return _probe_prompt(profile_url, probe)
    hint = (
        f'Our most recent message to them begins with: "{snippet}".'
        if snippet
        else "We have sent them at least one message before."
    )
    return f"""You are on LinkedIn, already logged in. Check whether a person has replied to us. READ-ONLY.

1. Navigate to: {profile_url}
2. If the page shows a login form or checkpoint, return {{"status": "failed", "error": "login_required"}}.
3. If there is no "Message" button (not connected), return {{"status": "no_thread", "error": null}}.
4. Click "Message" to open the conversation panel. Do NOT type anything.
5. Read the conversation from top (oldest) to bottom (newest). {hint}
   - If there is NO conversation at all, return {{"status": "no_thread", "error": null}}.
   - Locate OUR message (from the logged-in account). Everything ABOVE it is older
     history and does not count, even if it was written by them.
   - Look ONLY at messages BELOW ours. If any of them was written by the other person,
     return {{"status": "replied", "error": null, "reply_after_ours": true,
     "last_reply_text": "<first 200 chars of their newest message>"}}.
   - If the newest message in the thread is ours (nothing from them below it),
     return {{"status": "none", "error": null, "reply_after_ours": false,
     "last_reply_text": "<first 200 chars of their newest message above ours, or empty>"}}.
6. Close the conversation panel without sending anything.

Rules:
- Never type, never click Send. This task must not change anything.
{JSON_ONLY_RULE}"""


def _probe_prompt(profile_url: str, probe: str) -> str:
    """Did we already send a message that begins with `probe`? Used before a retry so a
    send whose confirmation was lost is not sent twice."""
    return f"""You are on LinkedIn, already logged in. Check whether WE already sent one specific message. READ-ONLY.

1. Navigate to: {profile_url}
2. If the page shows a login form or checkpoint, return {{"status": "failed", "error": "login_required"}}.
3. If there is no "Message" button, return {{"status": "not_sent", "error": null}}.
4. Click "Message" to open the conversation panel. Do NOT type anything.
5. Look at the messages written by us (the logged-in account) in this conversation.
   - If one of them begins with: "{probe}"
     return {{"status": "already_sent", "error": null}}.
   - Otherwise, or if there is no conversation at all, return {{"status": "not_sent", "error": null}}.
6. Close the conversation panel without sending anything.

Rules:
- Never type, never click Send. This task must not change anything.
{JSON_ONLY_RULE}"""
