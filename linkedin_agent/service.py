"""Every operation a front end can ask for, as plain async methods returning JSON-friendly
values. The MCP server is a thin wrapper over this; the CLI uses the pieces it shares.

Nothing here opens a browser. One-off LinkedIn actions are queued for the running
`linkedin-agent run` loop, which owns the one Chrome on the profile."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import reporting
from .adapters.csv_import import ImportResult, parse_leads, parse_rows
from .campaigns import (
    CampaignError,
    builtin_campaigns,
    load_all_user_campaigns,
    load_campaign,
    new_campaign_file,
    resolve_campaign_path,
)
from .config import Settings
from .core import messages as msg
from .core import sequence as seqeng
from .core.prompts import validate_linkedin_url
from .core.runner import Deps
from .models import Action, Campaign, LeadRecord, LeadStage, Task, TaskStatus, parse_duration
from .scheduler import resolve_review, restart_lead, retry_lead, skip_lead_step

HEARTBEAT_FILE = "run.json"
HEARTBEAT_STALE_S = 180

ONE_OFF_ACTIONS: dict[str, Action] = {
    "visit": Action.VISIT,
    "follow": Action.FOLLOW,
    "like": Action.LIKE_POST,
    "comment": Action.COMMENT_POST,
    "connect": Action.CONNECT,
    "message": Action.MESSAGE,
    "inmail": Action.INMAIL,
    "check_connection": Action.CHECK_CONNECTION,
    "check_replies": Action.CHECK_REPLIES,
    "withdraw": Action.WITHDRAW_INVITE,
}


class ServiceError(ValueError):
    """A user-facing problem: bad input, unknown lead, invalid campaign."""


@dataclass
class ImportSummary:
    imported: int
    new: int
    updated: int
    skipped: list[tuple[int, str]]
    sequences_started: int
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "imported": self.imported,
            "new": self.new,
            "updated": self.updated,
            "skipped": [{"row": r, "reason": why} for r, why in self.skipped],
            "sequences_started": self.sequences_started,
            "warnings": self.warnings,
        }


# ── run heartbeat (written by the CLI run loop, read by everyone else) ──────


def heartbeat_path(settings: Settings) -> Path:
    return settings.home / HEARTBEAT_FILE


def write_heartbeat(settings: Settings, account: str, started_at: datetime) -> None:
    data = {
        "pid": os.getpid(),
        "account": account,
        "started_at": started_at.isoformat(),
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "fast_test": settings.fast_test,
    }
    path = heartbeat_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def clear_heartbeat(settings: Settings) -> None:
    try:
        heartbeat_path(settings).unlink()
    except FileNotFoundError:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_state(settings: Settings, now: datetime | None = None) -> dict[str, Any]:
    """Is a `linkedin-agent run` loop active on this machine?"""
    now = now or datetime.now(timezone.utc)
    path = heartbeat_path(settings)
    if not path.exists():
        return {"active": False, "reason": "no run loop has reported in"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        beat = datetime.fromisoformat(data["heartbeat_at"])
    except (ValueError, KeyError, TypeError):
        return {"active": False, "reason": "heartbeat file unreadable"}
    age = (now - beat).total_seconds()
    alive = _pid_alive(int(data.get("pid", 0)))
    active = alive and age < HEARTBEAT_STALE_S
    return {
        "active": active,
        # Whether the loop that is up has windows and spacing off. Not the same as this
        # process's own setting: the run loop may have been started with a different env.
        "fast_test": bool(data.get("fast_test")),
        "pid": data.get("pid"),
        "account": data.get("account"),
        "started_at": data.get("started_at"),
        "heartbeat_at": data.get("heartbeat_at"),
        "heartbeat_age_s": int(age),
        "reason": None
        if active
        else ("process is gone" if not alive else f"last heartbeat {int(age)}s ago"),
    }


# ── the service ────────────────────────────────────────────────────────────


class Service:
    def __init__(self, deps: Deps, settings: Settings, account: str | None = None) -> None:
        self.deps = deps
        self.settings = settings
        self.account = account or settings.account
        self._from_disk: set[str] = set(load_all_user_campaigns(settings)) & set(deps.campaigns)

    def now(self) -> datetime:
        return self.deps.clock()

    # ── status & reporting ────────────────────────────────────────────

    async def status(self) -> dict[str, Any]:
        t = self.now()
        return {
            "run": run_state(self.settings, t),
            "account": await reporting.account_health(self.deps, self.account, t),
            "usage_today": await reporting.usage_today(self.deps, self.account, t),
            "queue": await reporting.queue_summary(self.deps, self.account),
            "leads_by_stage": await self.deps.leads.stage_counts(),
            "campaigns": sorted(self.deps.campaigns),
            "recent": [reporting.task_row(x) for x in await self.deps.queue.recent(10)],
        }

    async def report(self, campaign: str | None, since: str) -> dict[str, Any]:
        try:
            window = parse_duration(since)
        except ValueError as e:
            raise ServiceError(str(e)) from e
        r = await reporting.campaign_report(self.deps, self.account, campaign, window, self.now())
        r.pop("rows", None)
        return r

    async def log(self, limit: int = 50) -> list[dict[str, Any]]:
        return await self.deps.log.recent(self.account, None, limit)

    async def tasks(self, status: str | None, limit: int = 50) -> list[dict[str, Any]]:
        if status:
            try:
                st = TaskStatus(status)
            except ValueError as e:
                raise ServiceError(f"unknown task status {status!r}") from e
            rows = await self.deps.queue.list_by_status(st, limit)
        else:
            rows = await self.deps.queue.recent(limit)
        return [reporting.task_row(x) for x in rows]

    async def task(self, task_id: str) -> dict[str, Any]:
        t = await self.deps.queue.get(task_id)
        if t is None:
            raise ServiceError(f"no task {task_id!r}")
        return reporting.task_row(t)

    # ── leads ──────────────────────────────────────────────────────────

    async def leads(
        self,
        campaign: str | None = None,
        stage: str | None = None,
        search: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = await reporting.lead_rows(self.deps, campaign)
        if stage:
            rows = [r for r in rows if r["stage"] == stage]
        if search:
            q = search.lower()
            rows = [
                r
                for r in rows
                if q
                in " ".join(
                    str(r.get(k) or "")
                    for k in ("name", "company", "title", "headline", "linkedin_url")
                ).lower()
            ]
        return rows[:limit]

    async def _lead(self, key: str) -> LeadRecord:
        rec = await self.deps.leads.find(key)
        if rec is None:
            raise ServiceError(f"no lead matches {key!r}")
        return rec

    async def lead(self, key: str) -> dict[str, Any]:
        return await reporting.lead_detail(self.deps, await self._lead(key))

    async def preview(self, key: str) -> dict[str, Any]:
        rec = await self._lead(key)
        camp = self.deps.campaigns.get(rec.campaign)
        if camp is None:
            raise ServiceError(f"campaign {rec.campaign!r} is not loaded")
        out: dict[str, Any] = {
            "lead": rec.display_name,
            "campaign": camp.name,
            "profile_scraped": bool(rec.posts or rec.profile),
            "messages": {},
        }
        for name in camp.messages:
            r = await msg.render_message(name, rec, camp, self.deps.text_llm)
            out["messages"][name] = {
                "text": r.text,
                "hook": "model" if r.hook_used else ("fallback" if r.hook_fallback_used else None),
                "warnings": r.warnings,
            }
        return out

    async def import_leads(self, result: ImportResult, camp: Campaign) -> ImportSummary:
        errors, warnings = msg.campaign_check(camp, result.custom_columns)
        if errors:
            raise ServiceError("campaign has errors: " + "; ".join(errors))
        inserted, updated = await self.deps.leads.upsert_many(result.leads)
        now = self.now()
        started = 0
        for lead in result.leads:
            if await self.deps.leads.get_sequence(lead.id) is None:
                await self.deps.leads.save_sequence(seqeng.new_sequence(lead, camp, now))
                started += 1
        return ImportSummary(
            len(result.leads), inserted, updated, result.skipped, started, warnings
        )

    async def import_rows(self, rows: list[dict[str, Any]], campaign: str) -> ImportSummary:
        camp = self._campaign(campaign)
        return await self.import_leads(parse_rows(rows, camp.name, camp.default_timezone), camp)

    async def import_csv(self, path: str, campaign: str) -> ImportSummary:
        camp = self._campaign(campaign)
        p = Path(path).expanduser()
        if not p.exists():
            raise ServiceError(f"{p} does not exist")
        try:
            result = parse_leads(p, camp.name, camp.default_timezone)
        except ValueError as e:
            raise ServiceError(str(e)) from e
        return await self.import_leads(result, camp)

    async def retry(self, key: str) -> str:
        return await retry_lead(self.deps, await self._lead(key), self.now())

    async def skip(self, key: str) -> str:
        return await skip_lead_step(self.deps, await self._lead(key), self.now())

    async def restart(self, key: str, step: str | None = None) -> str:
        return await restart_lead(self.deps, await self._lead(key), self.now(), step)

    async def mark_handled(self, key: str) -> str:
        rec = await self._lead(key)
        rec.stage = LeadStage.DONE
        await self.deps.leads.update(rec)
        return f"{rec.display_name} marked done"

    async def inbox(self) -> list[dict[str, Any]]:
        return await reporting.inbox_rows(self.deps, self.now())

    # ── campaigns ──────────────────────────────────────────────────────

    def reload_campaigns(self) -> None:
        """Re-read the campaign files. Campaigns that were loaded from disk and have since
        been deleted drop out; ones registered in memory (tests, embedding) stay."""
        fresh = load_all_user_campaigns(self.settings)
        for name in self._from_disk - set(fresh):
            self.deps.campaigns.pop(name, None)
        self.deps.campaigns.update(fresh)
        self._from_disk = set(fresh)

    def _campaign(self, name: str) -> Campaign:
        self.reload_campaigns()
        camp = self.deps.campaigns.get(name)
        if camp is None:
            raise ServiceError(
                f"campaign {name!r} not found in {self.settings.campaigns_dir}; "
                f"known: {sorted(self.deps.campaigns) or 'none'}"
            )
        return camp

    async def campaigns(self) -> list[dict[str, Any]]:
        self.reload_campaigns()
        out = []
        for name, c in sorted(self.deps.campaigns.items()):
            out.append(
                {
                    "name": name,
                    "mode": c.mode,
                    "review_comments": c.review_comments,
                    "steps": [s.id for s in c.steps],
                    "paused": await self.deps.leads.is_paused(name),  # type: ignore[attr-defined]
                    "leads_by_stage": await self.deps.leads.stage_counts(name),
                    "path": str(self.settings.campaigns_dir / f"{name}.yaml"),
                }
            )
        return out

    def campaign_templates(self) -> list[str]:
        return [p.stem for p in builtin_campaigns()]

    def campaign_get(self, name: str) -> dict[str, Any]:
        try:
            path = resolve_campaign_path(name, self.settings)
            camp = load_campaign(path)
        except CampaignError as e:
            raise ServiceError(str(e)) from e
        errors, warnings = msg.campaign_check(camp)
        return {
            "name": camp.name,
            "path": str(path),
            "yaml": path.read_text(encoding="utf-8"),
            "errors": errors,
            "warnings": warnings,
            "steps": [
                {
                    "id": s.id,
                    "action": s.action.value,
                    "after": s.after,
                    "window": s.window,
                    "branch": s.branch,
                    "on_result": s.on_result,
                }
                for s in camp.steps
            ],
        }

    def campaign_check_text(self, yaml_text: str) -> dict[str, Any]:
        """Validate YAML text without writing it."""
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(yaml_text)
            tmp = Path(f.name)
        try:
            camp = load_campaign(tmp)
        except CampaignError as e:
            return {"ok": False, "errors": [str(e).replace(f"{tmp}: ", "")], "warnings": []}
        finally:
            tmp.unlink(missing_ok=True)
        errors, warnings = msg.campaign_check(camp)
        return {"ok": not errors, "errors": errors, "warnings": warnings, "name": camp.name}

    def campaign_write(self, name: str, yaml_text: str) -> dict[str, Any]:
        """Write a campaign file only if it validates. Returns the check result."""
        check = self.campaign_check_text(yaml_text)
        if not check["ok"]:
            raise ServiceError("not written: " + "; ".join(check["errors"]))
        if check.get("name") != name:
            raise ServiceError(f"the file says name: {check.get('name')!r}; expected {name!r}")
        self.settings.campaigns_dir.mkdir(parents=True, exist_ok=True)
        path = self.settings.campaigns_dir / f"{name}.yaml"
        path.write_text(yaml_text, encoding="utf-8")
        self.reload_campaigns()
        return {**check, "path": str(path)}

    def campaign_new(self, name: str, template: str = "default") -> dict[str, Any]:
        try:
            new_campaign_file(name, self.settings, template)
        except CampaignError as e:
            raise ServiceError(str(e)) from e
        self.reload_campaigns()
        return self.campaign_get(name)

    async def pause(self, campaign: str) -> str:
        self._campaign(campaign)
        n = await self.deps.leads.pause_campaign(campaign)  # type: ignore[attr-defined]
        ids = await self.deps.leads.lead_ids_for_campaign(campaign)  # type: ignore[attr-defined]
        c = await self.deps.queue.cancel_open_for_leads(ids, "paused")
        return f"Paused {n} sequence(s), cancelled {c} queued task(s)."

    async def resume(self, campaign: str) -> str:
        self._campaign(campaign)
        n = await self.deps.leads.resume_campaign(campaign, self.now())  # type: ignore[attr-defined]
        return f"Resumed {n} sequence(s)."

    # ── review ─────────────────────────────────────────────────────────

    async def review_pending(self) -> list[dict[str, Any]]:
        return [i.model_dump(mode="json") for i in await self.deps.review.pending()]

    async def review_decide(self, task_id: str, approve: bool, text: str | None) -> str:
        item = await self.deps.review.get(task_id)
        if item is None:
            raise ServiceError(f"no review item {task_id!r}")
        final = (text or item.draft) if approve else None
        return await resolve_review(self.deps, task_id, final, self.now())

    # ── account ────────────────────────────────────────────────────────

    async def breaker_reset(self) -> str:
        acct = await self.deps.accounts.get(self.account)
        acct.tripped_until, acct.trip_reason, acct.consecutive_failures = None, None, 0
        await self.deps.accounts.save(acct)
        return "Circuit breaker reset."

    # ── one-off actions: queued for the run loop ───────────────────────

    async def enqueue_action(
        self, action: str, url: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        act = ONE_OFF_ACTIONS.get(action)
        if act is None:
            raise ServiceError(f"unknown action {action!r}; one of {sorted(ONE_OFF_ACTIONS)}")
        try:
            url = validate_linkedin_url(url)
        except ValueError as e:
            raise ServiceError(str(e)) from e
        p = dict(params or {})
        if act in (Action.MESSAGE, Action.INMAIL) and not p.get("text"):
            raise ServiceError(f"{action} needs params.text")
        if act == Action.INMAIL and not p.get("subject"):
            raise ServiceError("inmail needs params.subject")
        if act == Action.COMMENT_POST and not p.get("text"):
            raise ServiceError("comment needs params.text (write it, or use a campaign step)")
        if act == Action.CONNECT:
            p.setdefault("note", "")
        lead = await self.deps.leads.find(url)
        p.setdefault("lead_name", lead.display_name if lead else url)
        task = Task(
            lead_id=lead.id if lead else None,
            action=act,
            profile_url=url,
            account=self.account,
            params=p,
            created_at=self.now(),
        )
        await self.deps.queue.enqueue(task)
        state = run_state(self.settings, self.now())
        return {
            "task_id": task.id,
            "queued": True,
            "run_active": state["active"],
            "note": None
            if state["active"]
            else "no run loop is active; start `linkedin-agent run` for this to execute",
        }


def format_import(summary: ImportSummary) -> str:
    lines = [
        f"Imported {summary.imported} leads ({summary.new} new, {summary.updated} updated, "
        f"{len(summary.skipped)} skipped)"
    ]
    lines += [f"  row {r}: {why}" for r, why in summary.skipped[:20]]
    lines.append(
        f"Sequences started: {summary.sequences_started}. Run `linkedin-agent run` to begin."
    )
    return "\n".join(lines)


def since_default(days: int = 30) -> timedelta:
    return timedelta(days=days)
