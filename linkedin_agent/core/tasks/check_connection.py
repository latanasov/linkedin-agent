from __future__ import annotations

from typing import Any

from ..prompts import JSON_ONLY_RULE, validate_linkedin_url


def build_prompt(profile_url: str, params: dict[str, Any]) -> str:
    profile_url = validate_linkedin_url(profile_url)
    return f"""You are on LinkedIn, already logged in. Check the connection state WITHOUT changing it.

1. Navigate to: {profile_url}
2. If the page shows a login form or checkpoint, return {{"status": "failed", "error": "login_required"}}.
3. Read the buttons in the profile header, then open the "More" menu and read it too. Report:
   - a "Pending" button, or "Pending" / "Withdraw" inside "More"
     -> {{"status": "pending", "error": null}}
   - "1st" degree badge next to the name, or a "Message" button with no "Connect" and no
     "Pending" anywhere -> {{"status": "connected", "error": null}}
   - a "Connect" button (in the header or inside "More") -> {{"status": "not_connected", "error": null}}
   - none of the above (no "Connect", no "Pending", no "1st" badge; for example a profile
     that only offers "Follow") -> {{"status": "no_option", "error": null}}
   A "Message" or "Follow" button on its own does not mean connected; check the badge.

Rules:
- This is READ-ONLY. Do NOT click Connect, Pending, Message, Follow or anything except
  opening the "More" menu to look.
{JSON_ONLY_RULE}"""
