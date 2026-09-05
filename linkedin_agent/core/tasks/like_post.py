from __future__ import annotations

from typing import Any

from ..prompts import JSON_ONLY_RULE, validate_linkedin_url, validate_post_url


def _target_block(profile_url: str, post_url: str | None, post_text: str) -> str:
    if post_url:
        validate_post_url(post_url)
        return f"1. Navigate to the post: {post_url}"
    hint = f' It begins with: "{post_text[:80]}"' if post_text else ""
    return (
        f"1. Navigate to: {profile_url.rstrip('/')}/recent-activity/all/ and find the newest post "
        f"authored by this person (not a repost, not a comment).{hint}"
    )


def build_prompt(profile_url: str, params: dict[str, Any]) -> str:
    profile_url = validate_linkedin_url(profile_url)
    post_url = params.get("post_url") or ""
    post_text = str(params.get("post_text") or "")
    return f"""You are on LinkedIn, already logged in. Like exactly one post.

{_target_block(profile_url, post_url, post_text)}
2. If the page shows a login form or checkpoint, return {{"status": "failed", "error": "login_required"}}.
3. If the post cannot be found, return {{"status": "post_not_found", "error": null}}.
4. Look at the "Like" button under the post. If it already shows as pressed/active
   (e.g. "Liked" or a filled thumb icon), return {{"status": "already_liked", "error": null, "post_url": "<url>"}}.
5. Otherwise click the "Like" button once (a plain Like, not another reaction).
6. Return {{"status": "liked", "error": null, "post_url": "<the post's url>"}}.

Rules:
- Do NOT comment, share, follow or connect. One click only.
- Do NOT retry more than once.
{JSON_ONLY_RULE}"""
