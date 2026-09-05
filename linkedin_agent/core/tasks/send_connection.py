from __future__ import annotations

from typing import Any

from ..prompts import JSON_ONLY_RULE, sanitize_user_text, validate_linkedin_url


def build_prompt(profile_url: str, params: dict[str, Any]) -> str:
    profile_url = validate_linkedin_url(profile_url)
    note = sanitize_user_text(str(params.get("note") or ""), max_length=300).strip()
    if note:
        note_steps = f"""4. Click "Add a note".
5. Type exactly (do not change it): {note}
6. Click "Send"."""
    else:
        note_steps = """4. If a dialog asks whether to add a note, choose "Send without a note".
5. If a dialog asks "How do you know this person?", select "Other" and continue.
6. Click "Send" if it has not been sent yet."""
    return f"""You are on LinkedIn, already logged in. Send one connection request.

1. Navigate to: {profile_url}
2. If the page shows a login form or checkpoint, return {{"status": "failed", "error": "login_required"}}.
3. Look at the buttons in the profile header AND open the "More" menu to see what it holds:
   - A "Pending" button in the header, or "Pending" / "Withdraw" inside "More": a request is
     already out. Return {{"status": "already_pending", "error": null}}.
   - A "1st" degree badge next to the name, or "Message" with no "Connect" and no "Pending"
     anywhere: you are already connected. Return {{"status": "already_connected", "error": null}}.
   - "Connect" in the header or inside "More": click it. A "Message" or "Follow" button next
     to it does NOT mean you are connected; a "2nd" or "3rd" badge means you are not.
   - None of these anywhere (for example a profile that only offers "Follow"):
     return {{"status": "cannot_connect", "error": null}}.
{note_steps}
7. If LinkedIn shows a message about reaching a limit, restrictions or unusual activity,
   return {{"status": "failed", "error": "restricted"}}.
8. When the request is sent (button now shows "Pending"), return {{"status": "sent", "error": null}}.

Rules:
- Do NOT modify the note text. Do NOT click Follow or Message.
- Do NOT retry more than once if Send fails.
{JSON_ONLY_RULE}"""
