"""FastAPI server — exposes all AIOS agents as HTTP endpoints for N8N/remote triggers."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncio

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from orchestrator.dashboard_api import router as dashboard_router, set_loop
from orchestrator.youtube_api import router as youtube_router

app = FastAPI(
    title="Expansao AI OS",
    description="Multi-agent orchestrator API — Expansao AI + CWI Software",
    version="0.1.0",
)

app.include_router(dashboard_router)
app.include_router(youtube_router)

# Serve static dashboard files
from pathlib import Path as _Path  # noqa: E402
if _Path("static").exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def _startup():
    set_loop(asyncio.get_event_loop())

_executor = ThreadPoolExecutor(max_workers=4)


# ── Request models ────────────────────────────────────────────────────────────

class RunAgentRequest(BaseModel):
    context: str = ""
    input_file: str = ""
    notion_page_id: str = ""
    days: int = 7


class PipelineRequest(BaseModel):
    context: str = ""
    start_from: str = ""


# ── Root ─────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return RedirectResponse(url="/youtube/")


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# ── Usage tracking ────────────────────────────────────────────────────────────

class TrackRequest(BaseModel):
    project: str                  # billable entity: "climate", "grc-flow", "aios", "cwi"
    agent_name: str
    model: str
    input_tokens: int
    output_tokens: int
    pipeline: str = ""
    duration_ms: int = 0


@app.post("/track", status_code=201)
def track(
    req: TrackRequest,
    x_aios_key: str | None = Header(default=None),
) -> dict:
    """
    Record LLM usage from any Expansao AI project.

    Usage (e.g. in Climate or GRC Flow):
        import httpx
        httpx.post(
            "https://aios.expansao-ai.com.br/track",
            headers={"X-AIOS-Key": "<TRACK_API_KEY>"},
            json={
                "project": "climate",
                "agent_name": "weather_analyzer",
                "model": "claude-sonnet-4-6",
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "duration_ms": 1500,
            },
        )
    """
    from orchestrator.settings import settings  # noqa: PLC0415
    from tools.usage_tracker import estimate_cost, log_run  # noqa: PLC0415

    if settings.track_api_key and x_aios_key != settings.track_api_key:
        raise HTTPException(status_code=403, detail="Invalid or missing X-AIOS-Key")

    cost = estimate_cost(req.model, req.input_tokens, req.output_tokens)
    log_run(
        project=req.project,
        pipeline=req.pipeline,
        agent_name=req.agent_name,
        model=req.model,
        input_tokens=req.input_tokens,
        output_tokens=req.output_tokens,
        cost_usd=cost,
        duration_ms=req.duration_ms,
    )
    return {"recorded": True, "cost_usd": round(cost, 6)}


@app.get("/usage/summary")
def usage_summary(days: int = 30) -> dict:
    """Return cost and token usage aggregated by project and agent for the past N days."""
    from tools.usage_tracker import query_summary  # noqa: PLC0415
    return query_summary(days=days)


# ── Status ────────────────────────────────────────────────────────────────────

@app.get("/status/cwi")
def status_cwi() -> dict:
    out = Path("outputs/cwi/")
    stages = {
        "meeting_secretary": "meeting_",
        "pmo":               "pmo_",
        "agile_coach":       "agile_coach_",
        "task_digest":       "digest_",
        "executive_report":  "executive_report_",
    }
    result = {}
    for key, prefix in stages.items():
        files = sorted(out.glob(f"{prefix}*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if out.exists() else []
        result[key] = files[0].name if files else None
    return result


@app.get("/status/expansao")
def status_expansao() -> dict:
    out = Path("outputs/")
    stages = ["ceo_plan", "pm_prds", "architect", "dev", "qa", "devops", "marketing"]
    result = {}
    for prefix in stages:
        files = sorted(out.glob(f"{prefix}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if out.exists() else []
        result[prefix] = files[0].name if files else None
    return result


# ── CWI Agents ────────────────────────────────────────────────────────────────

@app.post("/cwi/digest")
async def cwi_digest(background_tasks: BackgroundTasks) -> dict:
    """Trigger task digest — scans all meetings and publishes pending tasks to Notion."""
    background_tasks.add_task(_run_in_thread, _do_digest)
    return {"status": "triggered", "agent": "task_digest"}


@app.post("/cwi/pmo")
async def cwi_pmo(req: RunAgentRequest, background_tasks: BackgroundTasks) -> dict:
    """Trigger PMO Agent — generates weekly status report from CWI Meetings."""
    background_tasks.add_task(_run_in_thread, _do_pmo, req.context, req.days)
    return {"status": "triggered", "agent": "pmo"}


@app.post("/cwi/meeting-secretary")
async def cwi_meeting_secretary(req: RunAgentRequest, background_tasks: BackgroundTasks) -> dict:
    """Trigger Meeting Secretary — process a specific Notion transcription page."""
    if not req.notion_page_id:
        raise HTTPException(status_code=400, detail="notion_page_id is required")
    background_tasks.add_task(_run_in_thread, _do_meeting_secretary, req.notion_page_id)
    return {"status": "triggered", "agent": "meeting_secretary", "page_id": req.notion_page_id}


@app.post("/cwi/agile-coach")
async def cwi_agile_coach(req: RunAgentRequest, background_tasks: BackgroundTasks) -> dict:
    """Trigger Agile Coach Agent."""
    background_tasks.add_task(_run_in_thread, _do_agile_coach, req.input_file, req.context)
    return {"status": "triggered", "agent": "agile_coach"}


@app.post("/cwi/executive-reporting")
async def cwi_executive_reporting(req: RunAgentRequest, background_tasks: BackgroundTasks) -> dict:
    """Trigger Executive Reporting Agent."""
    background_tasks.add_task(_run_in_thread, _do_executive_reporting, req.context)
    return {"status": "triggered", "agent": "executive_reporting"}


# ── Expansao AI Agents ────────────────────────────────────────────────────────

@app.post("/expansao/run")
async def expansao_run(req: PipelineRequest, background_tasks: BackgroundTasks) -> dict:
    """Trigger the full Expansao AI pipeline."""
    background_tasks.add_task(_run_in_thread, _do_expansao_pipeline, req.context, req.start_from)
    return {"status": "triggered", "pipeline": "expansao", "start_from": req.start_from or "ceo"}


@app.post("/expansao/deploy-queue/execute")
async def deploy_queue_execute(background_tasks: BackgroundTasks) -> dict:
    """
    Called by N8N at 22:00. Deploys all deploy_ready stories, grouped by project.
    Skips silently if nothing is queued.
    """
    from tools.run_tracker import get_deploy_ready_stories  # noqa: PLC0415

    by_project = get_deploy_ready_stories()
    if not by_project:
        return {"skipped": True, "reason": "no stories in deploy queue"}

    background_tasks.add_task(_run_in_thread, _do_deploy_queue, by_project)
    summary = {p: len(s) for p, s in by_project.items()}
    return {"triggered": True, "projects": summary}


@app.post("/expansao/ceo")
async def expansao_ceo(req: RunAgentRequest, background_tasks: BackgroundTasks) -> dict:
    """Trigger CEO Agent only."""
    background_tasks.add_task(_run_in_thread, _do_ceo, req.context)
    return {"status": "triggered", "agent": "ceo"}


# ── DevOps webhook (called by N8N after deploy) ───────────────────────────────

class DeployCallbackRequest(BaseModel):
    service: str
    environment: str = "production"
    status: str  # success | failed
    url: str = ""
    logs: str = ""
    sprint: str = ""


@app.post("/devops/deploy-aios")
async def deploy_aios(background_tasks: BackgroundTasks) -> dict:
    """
    Triggered by N8N on push to main branch.
    DevOps Agent redeploys the AIOS API and Watcher on Coolify.
    """
    background_tasks.add_task(_run_in_thread, _do_deploy_aios)
    return {"status": "triggered", "action": "deploy_aios"}


@app.post("/devops/deploy-callback")
async def deploy_callback(req: DeployCallbackRequest) -> dict:
    """
    Called by N8N after a deploy finishes.
    Saves the result and notifies Slack.
    """
    from tools.notion import NotionClient  # noqa: PLC0415
    from tools.slack import post_slack_message  # noqa: PLC0415
    from orchestrator.settings import settings  # noqa: PLC0415

    deploy_data = req.model_dump()

    if settings.notion_projects_db_id and req.sprint:
        try:
            NotionClient().create_deploy_page(req.sprint, deploy_data)
        except Exception:
            pass

    icon = "[OK]" if req.status == "success" else "[FAIL]"
    if settings.slack_webhook_url_expansao:
        post_slack_message(
            f"{icon} *Deploy {req.service}* ({req.environment})\n"
            f"Status: {req.status}\n"
            f"URL: {req.url or 'N/A'}",
            channel="expansao",
        )

    return {"received": True, "service": req.service, "status": req.status}


# ── Worker functions (run in thread pool) ─────────────────────────────────────

def _run_in_thread(fn: Any, *args: Any) -> None:
    try:
        fn(*args)
    except Exception as e:
        import logging  # noqa: PLC0415
        logging.getLogger("aios.api").error(f"{fn.__name__} failed: {e}", exc_info=True)


def _do_digest() -> None:
    from orchestrator.agents_cwi.task_digest_agent import TaskDigestAgent  # noqa: PLC0415
    TaskDigestAgent().run(triggered_by="api")


def _do_pmo(context: str, days: int) -> None:
    from orchestrator.agents_cwi.pmo_agent import PMOAgent  # noqa: PLC0415
    PMOAgent().run(extra_context=context, days=days)


def _do_meeting_secretary(notion_page_id: str) -> None:
    from orchestrator.agents_cwi.meeting_secretary_agent import MeetingSecretaryAgent  # noqa: PLC0415
    MeetingSecretaryAgent().run(notion_page_id=notion_page_id)


def _do_agile_coach(input_file: str, context: str) -> None:
    from orchestrator.agents_cwi.agile_coach_agent import AgileCoachAgent  # noqa: PLC0415
    AgileCoachAgent().run(metrics_file=input_file, extra_context=context)


def _do_executive_reporting(context: str) -> None:
    from orchestrator.agents_cwi.executive_reporting_agent import ExecutiveReportingAgent  # noqa: PLC0415
    ExecutiveReportingAgent().run(extra_context=context)


def _do_expansao_pipeline(context: str, start_from: str) -> None:
    from orchestrator.pipeline import run_pipeline  # noqa: PLC0415
    run_pipeline(extra_context=context, start_from=start_from or "ceo")


def _do_ceo(context: str) -> None:
    from orchestrator.agents.ceo_agent import CEOAgent  # noqa: PLC0415
    CEOAgent().run(extra_context=context)


def _do_deploy_queue(by_project: dict) -> None:
    """Execute deploys per project for all deploy_ready stories."""
    from orchestrator.agents.devops_agent import DevOpsAgent  # noqa: PLC0415
    DevOpsAgent().run_deploy_queue(by_project)


def _do_deploy_aios() -> None:
    """DevOps Agent deploys AIOS API + Watcher to Coolify."""
    import httpx  # noqa: PLC0415
    from orchestrator.settings import settings  # noqa: PLC0415
    from tools.slack import post_slack_message  # noqa: PLC0415
    import logging  # noqa: PLC0415

    log = logging.getLogger("aios.devops")

    if not settings.coolify_api_key or not settings.coolify_base_url:
        log.warning("Coolify not configured — skipping deploy")
        return

    headers = {
        "Authorization": f"Bearer {settings.coolify_api_key}",
        "Content-Type": "application/json",
    }
    base = settings.coolify_base_url.rstrip("/")

    deployments = [
        ("aios API",     "nuq78y0fxb3toq3kdun7rb3u"),
        ("aios Watcher", "soox30s56xbhg0794ncwgkj4"),
    ]

    results = []
    for name, uuid in deployments:
        try:
            r = httpx.post(f"{base}/api/v1/deploy?uuid={uuid}&force=false",
                           headers=headers, timeout=30)
            r.raise_for_status()
            dep = r.json().get("deployments", [{}])[0]
            results.append(f"[OK] {name} → {dep.get('deployment_uuid','?')[:12]}")
        except Exception as e:
            results.append(f"[FAIL] {name}: {e}")

    summary = "\n".join(results)
    log.info(f"AIOS deploy triggered:\n{summary}")

    if settings.slack_webhook_url_expansao:
        post_slack_message(f"[DEVOPS] *Deploy AIOS disparado*\n{summary}", channel="expansao")
