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
#
# Why the checks before Send: on the first day of the long design-partner InMail, 9 sent
# and 8 failed with "send_button_not_found". The screenshots show the body typed and
# Send grey: the dialog had scrolled past the Subject field, the character counter was
# red, a "Personalize your message" tip covered the bottom of the dialog, and once the
# dialog opened on an earlier thread under a Salesforce "Is this the right CRM match?"
# card with the New InMail fields left empty. Send was there each time; it was disabled.


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
   in the profile header, next to the Message button) and click "View in Sales Navigator".
   If it opens in a new tab, continue there. If the menu has no such entry, click
   "Save in Sales Navigator" and then the Sales Navigator link it offers."""
    return f"""You are on LinkedIn, already logged in. Send one InMail through Sales Navigator.

1. Navigate to: {profile_url}
2. If the page shows a login form or checkpoint, return {{"status": "failed", "error": "login_required"}}.
   {MISSING_PROFILE_RULE}
{reach}
5. On the Sales Navigator lead page, click "Message". A compose dialog opens with a
   Subject field, a message body and a Send button. It may be titled "New message" with
   this person's name already shown at its top, and may say "Use 1 of N credits"; both
   are normal. If a dark "Personalize your message" tip or any other tip box appears,
   close it with its X. Ignore Salesforce/CRM panels ("Is this the right CRM match?",
   "Connect", "Log this conversation to CRM", "Contact not in CRM"): never click them.
6. If an earlier conversation is shown instead, note the first 100 characters of the most
   recent message written by THEM (you will return it as "prior_reply_text"; empty if none).
   The fields for a new InMail are then at the bottom, under "New InMail"; expand that
   section if it is collapsed and use its Subject and message fields.
7. Click the Subject field and type exactly: {subject}
8. Click the message body field to focus it and wait 1 second.
9. Type the message below EXACTLY (do not alter it, make sure the first character is not
   duplicated):
{message}
10. Check before sending. Scroll the dialog up to its top, then down to its bottom:
    - The Subject field shows the subject from step 7. If it is empty, click it and
      type the subject again.
    - The body holds the message exactly once. If it is empty, click into it and type the
      message. If the message appears twice, or the character counter near the bottom
      right (e.g. "1,121/1,900") is red, click into the body, select all of it (Ctrl+A),
      press Delete, and type the message once.
11. Find the "Send" button below the body field, at the bottom-right of the dialog. It
    turns from grey to blue once both Subject and body are filled and the counter is under
    the limit. If it is still grey after step 10, return
    {{"status": "failed", "error": "send_disabled: <what is wrong, e.g. subject empty, counter red at 2,240/1,900, other: ...>"}}.
12. Click "Send" and return {{"status": "sent", "error": null, "prior_reply_text": "<from step 6>"}}.

Rules:
- Never open the Messaging page, never click the compose (pencil) icon on the Messaging
  bar, and never search for the person by name: many people share a name and the InMail
  would go to a stranger. If a window with an empty "To"/name search field ever appears
  (one where you would have to pick the recipient), close it with its X and go back to
  step 5; never type into it.
- If the lead page has no Message button, or says you are out of InMail credits, return
  {{"status": "cannot_message", "error": null}}.
- Never press Enter to send; only the Send button sends.
- If you cannot find the Send button, return {{"status": "failed", "error": "send_button_not_found"}}.
- Do NOT modify the text. Do NOT send it twice. Do NOT retry more than once if Send fails.
{JSON_ONLY_RULE}"""
