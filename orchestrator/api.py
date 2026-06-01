"""FastAPI server — exposes all AIOS agents as HTTP endpoints for N8N/remote triggers."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(
    title="Expansao AI OS",
    description="Multi-agent orchestrator API — Expansao AI + CWI Software",
    version="0.1.0",
)

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


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


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
    if settings.slack_webhook_url:
        post_slack_message(
            f"{icon} *Deploy {req.service}* ({req.environment})\n"
            f"Status: {req.status}\n"
            f"URL: {req.url or 'N/A'}"
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
