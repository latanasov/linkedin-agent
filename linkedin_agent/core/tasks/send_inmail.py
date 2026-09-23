from __future__ import annotations

from typing import Any

from ..prompts import (
    JSON_ONLY_RULE,
    MISSING_PROFILE_RULE,
    sanitize_user_text,
    validate_linkedin_url,
)

# Why Sales Navigator and not the profile's own Message button: on linkedin.com that
# button opens the InMail compose inside the bottom-right messaging overlay, a cramped
# panel that shares the corner with whatever chat bubbles earlier tasks left open. Seen
# live over two days: 5 sent, 8 failed, and the failures ended with the model reaching
# for the overlay's compose icon, typing the person's name into a "New message" search
# and facing eight strangers with the same name. The Sales Navigator lead page opens a
# centred dialog with Subject, body and Send and nothing else to mistake it for.


def build_prompt(profile_url: str, params: dict[str, Any]) -> str:
    profile_url = validate_linkedin_url(profile_url)
    subject = sanitize_user_text(str(params.get("subject") or ""), max_length=200).strip()
    message = sanitize_user_text(
        str(params.get("text") or params.get("message") or ""), max_length=1900
    )
    if not subject or not message.strip():
        raise ValueError("inmail requires params.subject and params.text")
    on_sales_nav = "/sales/" in profile_url
    if on_sales_nav:
        reach = """3. You are already on the person's Sales Navigator lead page."""
    else:
        reach = """3. Close any small chat windows open at the bottom-right of the page (each has an X in
   its header) so nothing is in the way. Do not open the Messaging bar itself.
4. Get to this person's Sales Navigator lead page: open the "More" menu (the "..." button
   in the profile header, next to the Message button) and click "View in Sales
   Navigator". If it opens in a new tab, continue there. If the menu has no such entry,
   click "Save in Sales Navigator" and then the Sales Navigator link it offers."""
    return f"""You are on LinkedIn, already logged in. Send one InMail through Sales Navigator.

1. Navigate to: {profile_url}
2. If the page shows a login form or checkpoint, return {{"status": "failed", "error": "login_required"}}.
   {MISSING_PROFILE_RULE}
{reach}
5. On the Sales Navigator lead page, click "Message". A compose dialog opens in the middle
   of the page with a Subject field, a message body and a Send button. It may mention how
   many InMail credits you have; that is normal.
6. If an existing conversation is shown instead, note the first 100 characters of the most
   recent message written by THEM (you will return it as "prior_reply_text"; empty if none).
7. Click the Subject field and type exactly: {subject}
8. Click the message body field to focus it and wait 1 second.
9. Type the message below EXACTLY (do not alter it, make sure the first character is not
   duplicated):
{message}
10. Check that the text is visible inside the body field; if it is empty, click into it
    and type the message once more.
11. Find the "Send" button directly below the body field, at the bottom of the compose
    dialog. It becomes active once the field has text. Scroll the dialog if needed.
12. Click "Send" and return {{"status": "sent", "error": null, "prior_reply_text": "<from step 6>"}}.

Rules:
- Never open the Messaging page, never click the compose (pencil) icon on the Messaging
  bar, and never search for the person by name: many people share a name and the InMail
  would go to a stranger. If a window titled "New message" with a name search field ever
  appears, close it with its X and go back to step 5; never type into it.
- If the lead page has no Message button, or says you are out of InMail credits, return
  {{"status": "cannot_message", "error": null}}.
- Never press Enter to send; only the Send button sends.
- If you cannot find the Send button, return {{"status": "failed", "error": "send_button_not_found"}}.
- Do NOT modify the text. Do NOT send it twice. Do NOT retry more than once if Send fails.
{JSON_ONLY_RULE}"""
