from __future__ import annotations

from typing import Any

from ..prompts import (
    JSON_ONLY_RULE,
    MISSING_PROFILE_RULE,
    sanitize_user_text,
    validate_linkedin_url,
)


def build_prompt(profile_url: str, params: dict[str, Any]) -> str:
    profile_url = validate_linkedin_url(profile_url)
    note = sanitize_user_text(str(params.get("note") or ""), max_length=300).strip()
    if note:
        note_steps = f"""4. Click "Add a note" if you see it. LinkedIn has several variants
   of this dialog and some open the note box directly: whatever you see, get to a box you
   can type the note into, and do not click "Send without a note".
5. Click inside the note box so it is focused, then type exactly (do not change it): {note}
6. Look at the box and its character counter. If the box is still empty or the counter
   still reads 0, click into it and type the note once more.
7. If the note is in the box: wait 1 second, look at the dialog again, then click its
   "Send" button. If "Send" is grey or nothing happens, click at the end of the note,
   type one space, delete it, and click "Send" again.
8. If the box is STILL empty after the second try, stop typing and send the invitation
   without a note instead: if the dialog offers "Send without a note", click it;
   otherwise close this dialog with Cancel or X, click "Connect" again and choose
   "Send without a note". When the button shows "Pending", return
   {{"status": "sent_without_note", "error": null}}."""
    else:
        note_steps = """4. If a dialog asks whether to add a note, choose "Send without a note".
5. If a dialog asks "How do you know this person?", select "Other" and continue.
6. Click "Send" if it has not been sent yet.
7. (nothing more to do here)
8. (nothing more to do here)"""
    return f"""You are on LinkedIn, already logged in. Send one connection request.

1. Navigate to: {profile_url}
2. If the page shows a login form or checkpoint, return {{"status": "failed", "error": "login_required"}}.
   {MISSING_PROFILE_RULE}
3. Look at the buttons in the profile header AND open the "More" menu to see what it holds:
   - A "Pending" button in the header, or "Pending" / "Withdraw" inside "More": a request is
     already out. Return {{"status": "already_pending", "error": null}}.
   - A "1st" degree badge next to the name, or "Message" with no "Connect" and no "Pending"
     anywhere: you are already connected. Return {{"status": "already_connected", "error": null}}.
   - "Connect" in the header or inside "More": click it, then wait 2 seconds. A dialog
     animates in and every element index you read before the click is now stale: look at
     the page again before your next action. A "Message" or "Follow" button next to
     Connect does NOT mean you are connected; a "2nd" or "3rd" badge means you are not.
   - None of these anywhere (for example a profile that only offers "Follow"):
     return {{"status": "cannot_connect", "error": null}}.
{note_steps}
9. If LinkedIn shows a message about reaching a limit, restrictions or unusual activity,
   return {{"status": "failed", "error": "restricted"}}.
10. When the request is sent with the note (button now shows "Pending"), return
    {{"status": "sent", "error": null}}.

Rules:
- Do NOT modify the note text. Do NOT click Follow or Message.
- Do NOT retry more than once if Send fails.
{JSON_ONLY_RULE}"""
