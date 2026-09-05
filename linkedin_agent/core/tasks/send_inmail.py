from __future__ import annotations

from typing import Any

from ..prompts import JSON_ONLY_RULE, sanitize_user_text, validate_linkedin_url


def build_prompt(profile_url: str, params: dict[str, Any]) -> str:
    profile_url = validate_linkedin_url(profile_url)
    subject = sanitize_user_text(str(params.get("subject") or ""), max_length=200).strip()
    message = sanitize_user_text(
        str(params.get("text") or params.get("message") or ""), max_length=1900
    )
    if not subject or not message.strip():
        raise ValueError("inmail requires params.subject and params.text")
    context = "LinkedIn Sales Navigator" if "/sales/" in profile_url else "LinkedIn"
    return f"""You are on {context}, already logged in. Send one InMail.

1. Navigate to: {profile_url}
2. If the page shows a login form or checkpoint, return {{"status": "failed", "error": "login_required"}}.
3. Click the "Message" or "InMail" button on the profile.
4. If an existing conversation is shown, note the first 100 characters of the most
   recent message written by THEM (you will return it as "prior_reply_text"; empty if none).
   If there is a Subject field, type exactly: {subject}
5. Click the message body field to focus it and wait 1 second.
6. Type the message below EXACTLY (do not alter it, make sure the first character is not
   duplicated):
{message}
7. Check that the text is visible inside the body field; if it is empty, click into it
   and type the message once more.
8. Find the "Send" button directly below the body field, at the bottom of the compose
   panel. It becomes active once the field has text. Scroll the panel if needed.
9. Click "Send" and return {{"status": "sent", "error": null, "prior_reply_text": "<from step 4>"}}.

Rules:
- If no Message/InMail button exists or InMail credits are exhausted, return
  {{"status": "cannot_message", "error": null}}.
- Never press Enter to send; only the Send button sends.
- If you cannot find the Send button, return {{"status": "failed", "error": "send_button_not_found"}}.
- Do NOT modify the text. Do NOT send it twice. Do NOT retry more than once if Send fails.
{JSON_ONLY_RULE}"""
