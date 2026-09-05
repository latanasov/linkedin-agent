"""Local web UI: a single page over the same SQLite state the CLI reads.

Bound to localhost only; there is no authentication because there is no network.
Reads go through `reporting`; the few actions (retry, skip, restart, mark handled,
review decisions, pause/resume, breaker reset) call the same scheduler helpers the
CLI commands use, so both front ends behave identically.
"""

from __future__ import annotations

import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .. import __version__, reporting
from ..bootstrap import App
from ..models import LeadStage, parse_duration
from ..scheduler import resolve_review, restart_lead, retry_lead, skip_lead_step

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class ReviewDecision(BaseModel):
    approve: bool
    text: str | None = None


class RestartBody(BaseModel):
    step: str | None = None


def create_ui_app(app: App, now: Any = None) -> FastAPI:
    deps = app.deps
    account = app.settings.account
    clock = now or (lambda: datetime.now(timezone.utc))
    api = FastAPI(title="linkedin-agent", version=__version__, docs_url=None, redoc_url=None)

    async def _lead(key: str) -> Any:
        rec = await deps.leads.find(key)
        if rec is None:
            raise HTTPException(404, f"no lead matches {key!r}")
        return rec

    @api.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @api.get("/api/overview")
    async def overview() -> dict[str, Any]:
        t = clock()
        return {
            "now": t.isoformat(),
            "version": __version__,
            "fast_test": deps.settings.fast_test,
            "account": await reporting.account_health(deps, account, t),
            "usage": await reporting.usage_today(deps, account, t),
            "queue": await reporting.queue_summary(deps, account),
            "stages": await deps.leads.stage_counts(),
            "campaigns": sorted(deps.campaigns),
            "recent": [reporting.task_row(x) for x in await deps.queue.recent(15)],
        }

    @api.get("/api/leads")
    async def leads(campaign: str | None = None) -> list[dict[str, Any]]:
        return await reporting.lead_rows(deps, campaign)

    @api.get("/api/leads/{key}")
    async def lead(key: str) -> dict[str, Any]:
        return await reporting.lead_detail(deps, await _lead(key))

    @api.post("/api/leads/{key}/retry")
    async def lead_retry(key: str) -> dict[str, str]:
        return {"message": await retry_lead(deps, await _lead(key), clock())}

    @api.post("/api/leads/{key}/skip")
    async def lead_skip(key: str) -> dict[str, str]:
        return {"message": await skip_lead_step(deps, await _lead(key), clock())}

    @api.post("/api/leads/{key}/restart")
    async def lead_restart(key: str, body: RestartBody | None = None) -> dict[str, str]:
        step = body.step if body else None
        return {"message": await restart_lead(deps, await _lead(key), clock(), step)}

    @api.post("/api/leads/{key}/handled")
    async def lead_handled(key: str) -> dict[str, str]:
        rec = await _lead(key)
        rec.stage = LeadStage.DONE
        await deps.leads.update(rec)
        return {"message": f"{rec.display_name} marked done"}

    @api.get("/api/inbox")
    async def inbox() -> list[dict[str, Any]]:
        return await reporting.inbox_rows(deps, clock())

    @api.get("/api/tasks")
    async def tasks(
        status: str | None = None, limit: int = Query(50, le=500)
    ) -> list[dict[str, Any]]:
        from ..models import TaskStatus

        if status:
            try:
                st = TaskStatus(status)
            except ValueError as e:
                raise HTTPException(400, f"unknown status {status!r}") from e
            rows = await deps.queue.list_by_status(st, limit)
        else:
            rows = await deps.queue.recent(limit)
        return [reporting.task_row(x) for x in rows]

    @api.get("/api/log")
    async def log(limit: int = Query(100, le=1000)) -> list[dict[str, Any]]:
        return await deps.log.recent(account, None, limit)

    @api.get("/api/review")
    async def review() -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in await deps.review.pending()]

    @api.post("/api/review/{task_id}")
    async def review_decide(task_id: str, body: ReviewDecision) -> dict[str, str]:
        item = await deps.review.get(task_id)
        if item is None:
            raise HTTPException(404, "no such review item")
        text = (body.text or item.draft) if body.approve else None
        return {"message": await resolve_review(deps, task_id, text, clock())}

    @api.get("/api/report")
    async def report(campaign: str | None = None, since: str = "30d") -> dict[str, Any]:
        try:
            window = parse_duration(since)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return await reporting.campaign_report(deps, account, campaign, window, clock())

    @api.get("/api/campaigns")
    async def campaigns() -> list[dict[str, Any]]:
        out = []
        for name, c in sorted(deps.campaigns.items()):
            out.append(
                {
                    "name": name,
                    "mode": c.mode,
                    "steps": [
                        {
                            "id": s.id,
                            "action": s.action.value,
                            "after": s.after,
                            "window": s.window,
                            "branch": s.branch,
                        }
                        for s in c.steps
                    ],
                    "paused": await deps.leads.is_paused(name),  # type: ignore[attr-defined]
                    "leads": (await deps.leads.stage_counts(name)),
                }
            )
        return out

    @api.post("/api/campaigns/{name}/pause")
    async def campaign_pause(name: str) -> dict[str, str]:
        if name not in deps.campaigns:
            raise HTTPException(404, f"campaign {name!r} not loaded")
        n = await deps.leads.pause_campaign(name)  # type: ignore[attr-defined]
        ids = await deps.leads.lead_ids_for_campaign(name)  # type: ignore[attr-defined]
        c = await deps.queue.cancel_open_for_leads(ids, "paused")
        return {"message": f"Paused {n} sequence(s), cancelled {c} queued task(s)."}

    @api.post("/api/campaigns/{name}/resume")
    async def campaign_resume(name: str) -> dict[str, str]:
        if name not in deps.campaigns:
            raise HTTPException(404, f"campaign {name!r} not loaded")
        n = await deps.leads.resume_campaign(name, clock())  # type: ignore[attr-defined]
        return {"message": f"Resumed {n} sequence(s)."}

    @api.post("/api/breaker/reset")
    async def breaker_reset() -> dict[str, str]:
        acct = await deps.accounts.get(account)
        acct.tripped_until, acct.trip_reason, acct.consecutive_failures = None, None, 0
        await deps.accounts.save(acct)
        return {"message": "Circuit breaker reset."}

    @api.exception_handler(HTTPException)
    async def _http_error(_: Any, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return api


async def serve(
    app: App, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, open_browser: bool = True
) -> None:
    """Run the UI until interrupted. Localhost only by design."""
    import uvicorn

    config = uvicorn.Config(create_ui_app(app), host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    url = f"http://{host}:{port}/"
    if open_browser:
        webbrowser.open(url)
    await server.serve()
