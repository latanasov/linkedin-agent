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
# The next day the same leads (Keith, Heidi) failed again while others went out: Sales
# Navigator keeps an unsent InMail as a draft, so each retry typed the message after the
# one left from the last attempt, the counter went past 1,900 and Send stayed grey. The
# fields are now emptied before typing. Jill's screenshot then showed the body typed once,
# the counter at "1,114 / 1,900" (shown in red at any length) and Send grey: the Subject,
# scrolled out of view above the body, was empty. The Subject is now typed after the body,
# by scrolling up to it, and the counter is read by its numbers, not its colour. Paul's failure was another shape: an Open Profile,
# whose own Message button opened a window already addressed to him ("Free message"),
# Subject and body filled, and a round blue paper-plane icon where the model looked for
# a button labelled "Send".
# Overnight on 2 Oct three InMails in a row ended "unable to close the messaging overlay":
# the model took the Messaging panel, which has no X, for a chat window it had to close
# first, and gave up. Closing chat windows is now optional and never a reason to stop.


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
        reach = """3. If small chat windows for other people are open at the bottom-right, you may close
   them with the X in their header; this is optional. The "Messaging" panel itself has no
   X and cannot be closed: leave it as it is (at most collapse it with its arrow), do not
   click anything inside it, and never stop or fail because of it. If a window will not
   close after one try, ignore it and go on to step 4.
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
   If the person has an Open Profile, their own "Message" button may instead open a
   window already addressed to them (their name at its top, "Free message" under it).
   That is fine: use its Subject and message fields the same way.
6. If an earlier conversation is shown instead, note the first 100 characters of the most
   recent message written by THEM (you will return it as "prior_reply_text"; empty if none).
   The fields for a new InMail are then at the bottom, under "New InMail"; expand that
   section if it is collapsed and use its Subject and message fields.
7. Click the message body field, select everything in it (Ctrl+A) and press Delete so it
   is empty. Sales Navigator keeps an unsent draft from an earlier attempt, and it must not
   stay in front of the message. Wait 1 second.
8. Type the message below EXACTLY (do not alter it, make sure the first character is not
   duplicated):
{message}
9. Now the Subject. It is a separate single-line field ABOVE the message body, with the
   placeholder "Subject (required)"; once the body is long the dialog scrolls it out of
   view. Scroll the dialog up until you can see it, click it, select everything in it
   (Ctrl+A), press Delete, and type exactly: {subject}
10. Check before sending:
    - The Subject field holds the subject from step 9. An empty Subject is the usual
      reason Send stays grey.
    - The body holds the message exactly once. If it is empty, click into it and type the
      message. If the message appears twice, click into the body, select all of it
      (Ctrl+A), press Delete, and type the message once.
    - The character counter under the body (e.g. "1,114 / 1,900") can be red or orange at
      any length; the colour means nothing. The message is too long only if the first
      number is larger than the second.
11. Find the "Send" button below the body field, at the bottom-right of the dialog. It
    may be a button labelled "Send" or a round blue paper-plane icon with no text; both
    are the Send button. It turns from grey to blue once both Subject and body are filled.
    If it is still grey after step 10, return
    {{"status": "failed", "error": "send_disabled: <what is wrong, e.g. subject empty, counter at 2,240 / 1,900, other: ...>"}}.
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
