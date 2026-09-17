from __future__ import annotations

from typing import Any

from ..prompts import JSON_ONLY_RULE, MISSING_PROFILE_RULE, validate_linkedin_url


def build_prompt(profile_url: str, params: dict[str, Any]) -> str:
    profile_url = validate_linkedin_url(profile_url)
    return f"""You are on LinkedIn, already logged in. Check the connection state WITHOUT changing it.

1. Navigate to: {profile_url}
2. If the page shows a login form or checkpoint, return {{"status": "failed", "error": "login_required"}}.
   {MISSING_PROFILE_RULE}
3. Find the degree badge printed next to the person's name: "1st", "2nd", "3rd" or "3rd+".
   It is the only reliable statement of the connection, so read it before anything else.
4. Read the buttons in the profile header, then open the "More" menu and read it too. Report:
   - the badge says "1st" -> {{"status": "connected", "error": null}}
   - any other badge ("2nd", "3rd", "3rd+") and a "Pending" button, or "Pending" /
     "Withdraw" inside "More" -> {{"status": "pending", "error": null}}
   - any other badge and a "Connect" button (in the header or inside "More")
     -> {{"status": "not_connected", "error": null}}
   - none of the above, or you cannot find the badge at all
     -> {{"status": "no_option", "error": null}}
   A "Message" button does NOT mean connected: LinkedIn shows it on open profiles and to
   Sales Navigator, and an invitation that is still pending often shows it too. Never
   answer "connected" without having seen the "1st" badge with your own eyes.

Rules:
- This is READ-ONLY. Do NOT click Connect, Pending, Message, Follow or anything except
  opening the "More" menu to look.
{JSON_ONLY_RULE}"""
