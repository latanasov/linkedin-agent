from __future__ import annotations

from typing import Any

from ..prompts import JSON_ONLY_RULE, sanitize_user_text, validate_linkedin_url


def build_prompt(profile_url: str, params: dict[str, Any]) -> str:
    profile_url = validate_linkedin_url(profile_url)
    message = sanitize_user_text(
        str(params.get("text") or params.get("message") or ""), max_length=1900
    )
    if not message.strip():
        raise ValueError("message requires params.text")
    return f"""You are on LinkedIn, already logged in. Send one direct message to a 1st-degree connection.

1. Navigate to: {profile_url}
2. If the page shows a login form or checkpoint, return {{"status": "failed", "error": "login_required"}}.
3. If the header shows "Pending" or "Connect" instead of "Message", you are not connected:
   return {{"status": "not_connected", "error": null}}.
4. Click the "Message" button in the profile header. A conversation panel opens, usually
   as an overlay at the bottom-right of the page. If it is collapsed to a small header
   bar, click that header once to expand it.
5. Before typing, read the thread: note the first 100 characters of the most recent
   message written by THEM, if any. You will return it as "prior_reply_text" (empty
   string if they never wrote anything).
6. Click the message compose field ("Write a message…") to focus it and wait 1 second.
7. Type the message below EXACTLY (do not alter it, make sure the first character is not
   duplicated):
{message}
8. Check that the text is now visible inside the compose field. If the field is still
   empty, click into it and type the message once more.
9. Find the "Send" button. It is directly BELOW the compose field, at the bottom-right of
   the conversation panel, next to the emoji/attachment icons. It turns from grey to blue
   once the field has text. If it is not visible, scroll the conversation panel to the
   bottom or expand the panel; do not look in the profile header for it.
10. Click "Send".
11. When the message appears in the conversation thread above the compose field, return
    {{"status": "sent", "error": null, "prior_reply_text": "<from step 5>"}}.

Rules:
- If no Message button exists or the compose field never appears, return
  {{"status": "cannot_message", "error": null}}.
- Never press Enter to send: the message has line breaks and Enter may send a partial
  message. Only the Send button sends.
- If you still cannot find the Send button after step 9, return
  {{"status": "failed", "error": "send_button_not_found"}} without pressing anything else.
- Do NOT modify the message text. Do NOT send it twice.
- Do NOT retry more than once if Send fails.
{JSON_ONLY_RULE}"""
