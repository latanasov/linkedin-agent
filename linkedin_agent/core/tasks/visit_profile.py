from __future__ import annotations

from typing import Any

from ..prompts import JSON_ONLY_RULE, validate_linkedin_url


def build_prompt(profile_url: str, params: dict[str, Any]) -> str:
    profile_url = validate_linkedin_url(profile_url)
    max_posts = int(params.get("max_posts", 3))
    return f"""You are on LinkedIn, already logged in. Read one profile and report what you see.

1. Navigate to: {profile_url}
2. Wait for the page to load. If it shows a login form, an "authwall", or a security
   checkpoint, stop and return {{"status": "failed", "error": "login_required"}}.
3. From the profile header and About section, read:
   - full_name
   - headline (the line under the name)
   - title (current job title) and company (current employer)
   - location (city/country line under the headline)
   - about (first 500 characters of the About section, if present)
   - connection_degree ("1st", "2nd" or "3rd")
   - company_page_url (link of the current company, if visible)
4. Scroll down once to the "Activity" section. For up to {max_posts} of the newest items
   authored by this person (skip reposts and comments), record:
   - url (the post link, if you can get it; otherwise "")
   - posted_days_ago (an integer: "2d" -> 2, "1w" -> 7, "3w" -> 21, "1mo" -> 30, "2yr" -> 730; if unknown use null)
   - text (first 300 characters of the post)
   If the Activity section shows no posts, return an empty list.
5. Do NOT click Connect, Follow, Message or any other button. Do NOT like anything.

Return exactly this JSON shape:
{{"status": "ok", "full_name": "", "headline": "", "title": "", "company": "", "location": "",
  "about": "", "connection_degree": "", "company_page_url": "",
  "posts": [{{"url": "", "posted_days_ago": 0, "text": ""}}]}}
{JSON_ONLY_RULE}"""
