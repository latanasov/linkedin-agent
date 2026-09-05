from __future__ import annotations

from typing import Any

from ..prompts import JSON_ONLY_RULE, sanitize_user_text, validate_linkedin_url, validate_post_url


def build_prompt(profile_url: str, params: dict[str, Any]) -> str:
    profile_url = validate_linkedin_url(profile_url)
    text = sanitize_user_text(str(params.get("text") or ""), max_length=600)
    if not text.strip():
        raise ValueError("comment_post requires params.text")
    post_url = params.get("post_url") or ""
    post_text = str(params.get("post_text") or "")
    if post_url:
        validate_post_url(post_url)
        target = f"1. Navigate to the post: {post_url}"
    else:
        hint = f' It begins with: "{post_text[:80]}"' if post_text else ""
        target = (
            f"1. Navigate to: {profile_url.rstrip('/')}/recent-activity/all/ and open the newest post "
            f"authored by this person (not a repost).{hint}"
        )
    return f"""You are on LinkedIn, already logged in. Post exactly one comment.

{target}
2. If the page shows a login form or checkpoint, return {{"status": "failed", "error": "login_required"}}.
3. If the post cannot be found or comments are disabled, return {{"status": "post_not_found", "error": null}}.
4. Open the comments under the post and look for one written by the logged-in account
   (you). If there already is one, do NOT post again: return
   {{"status": "already_commented", "error": null, "post_url": "<the post's url>"}}.
   Then click the "Comment" button under the post so the comment box is focused. Wait 1 second.
5. Type the comment below EXACTLY as written, character by character (do not paste, do not
   change wording, do not add anything before or after):
{text}
6. Click the "Post" (or "Comment") button to submit.
7. Return {{"status": "commented", "error": null, "post_url": "<the post's url>"}}.

Rules:
- If the comment box never appears, return {{"status": "cannot_comment", "error": null}}.
- Do NOT like, share, follow or connect. Never post the same comment twice.
- Do NOT retry more than once if Post fails.
{JSON_ONLY_RULE}"""
