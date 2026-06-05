"""Dashboard API router — auth, run tracking, SSE, gate intervention."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
_bearer = HTTPBearer(auto_error=False)

# ── Auth ──────────────────────────────────────────────────────────────────────

def _make_token(user: str) -> str:
    from orchestrator.settings import settings  # noqa: PLC0415
    import hmac, hashlib, base64  # noqa: PLC0415
    exp = int((datetime.now(timezone.utc) + timedelta(hours=24)).timestamp())
    payload = f"{user}:{exp}"
    sig = hmac.new(settings.dashboard_secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def _verify_token(token: str) -> str | None:
    from orchestrator.settings import settings  # noqa: PLC0415
    import hmac, hashlib, base64  # noqa: PLC0415
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        user, exp_str, sig = decoded.rsplit(":", 2)
        if int(exp_str) < int(datetime.now(timezone.utc).timestamp()):
            return None
        expected = hmac.new(
            settings.dashboard_secret_key.encode(),
            f"{user}:{exp_str}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return user
    except Exception:
        return None


def _auth(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    if not creds:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = _verify_token(creds.credentials)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return user


# ── SSE event bus ─────────────────────────────────────────────────────────────

_listeners: list[asyncio.Queue] = []


async def broadcast(event: dict) -> None:
    dead = []
    for q in _listeners:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _listeners.remove(q)


def emit_event(event: dict) -> None:
    """Thread-safe emit from sync pipeline code."""
    import threading  # noqa: PLC0415
    loop = _get_loop()
    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(broadcast(event), loop)


_main_loop: asyncio.AbstractEventLoop | None = None


def _get_loop() -> asyncio.AbstractEventLoop | None:
    return _main_loop


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop


# ── Login ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(req: LoginRequest) -> dict:
    from orchestrator.settings import settings  # noqa: PLC0415
    if req.username != settings.dashboard_user or req.password != settings.dashboard_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return {"token": _make_token(req.username), "user": req.username}


# ── Run management ─────────────────────────────────────────────────────────────

@router.get("/runs")
def list_runs(user: str = Depends(_auth)) -> list[dict]:
    from tools.run_tracker import get_runs  # noqa: PLC0415
    runs = get_runs(limit=50)
    return [_serialize(r) for r in runs]


@router.get("/runs/{run_id}")
def get_run(run_id: str, user: str = Depends(_auth)) -> dict:
    from tools.run_tracker import get_run_detail  # noqa: PLC0415
    detail = get_run_detail(run_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Run not found")
    return _serialize(detail)


@router.get("/costs")
def get_costs(user: str = Depends(_auth)) -> dict:
    from tools.run_tracker import get_cost_summary  # noqa: PLC0415
    from tools.usage_tracker import query_summary  # noqa: PLC0415
    runs_summary = get_cost_summary()
    agent_summary = query_summary(days=30)
    return {"runs": runs_summary, "agents": agent_summary}


@router.get("/projects")
def list_projects() -> list[dict]:
    from tools.project_registry import get_projects  # noqa: PLC0415
    return get_projects()


@router.get("/stories")
def list_stories(sprint: str | None = None, project: str | None = None,
                 user: str = Depends(_auth)) -> list[dict]:
    from tools.run_tracker import get_stories  # noqa: PLC0415
    stories = get_stories(sprint=sprint)
    if project:
        stories = [s for s in stories if s.get("project") == project]
    return _serialize(stories)


@router.get("/stories/sprints")
def list_sprints(user: str = Depends(_auth)) -> list[str]:
    from tools.run_tracker import _conn  # noqa: PLC0415
    c = _conn()
    if not c:
        return []
    try:
        with c.cursor() as cur:
            cur.execute("SELECT DISTINCT sprint FROM pipeline_stories ORDER BY sprint DESC")
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


class StoryStatusUpdate(BaseModel):
    status: str  # "deploy_ready" | "dev" | "qa" | etc.


## ── Backlog ───────────────────────────────────────────────────────────────────

@router.get("/backlog")
def get_backlog(project: str | None = None, user: str = Depends(_auth)) -> list[dict]:
    """Fetch backlog items from Notion, optionally filtered by project."""
    try:
        from tools.notion import NotionClient  # noqa: PLC0415
        notion = NotionClient()
        # Map dashboard project slug to Notion Project select name
        from tools.project_registry import get_notion_name  # noqa: PLC0415
        notion_project = get_notion_name(project) if project else None
        items = notion.get_backlog(project=notion_project)
        return _serialize(items)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class MarkSprintRequest(BaseModel):
    sprint: str = ""


@router.post("/backlog/{notion_id}/sprint")
async def mark_backlog_for_sprint(notion_id: str, body: MarkSprintRequest,
                                   user: str = Depends(_auth)) -> dict:
    """Mark a backlog item as Ready and assign to sprint."""
    try:
        from tools.notion import NotionClient  # noqa: PLC0415
        notion = NotionClient()
        notion._patch(f"/pages/{notion_id}", {
            "properties": {
                "Status": {"select": {"name": "Ready"}},
                "Sprint": {"rich_text": [{"text": {"content": body.sprint}}]},
            }
        })
        await broadcast({"type": "backlog_update", "notion_id": notion_id})
        return {"ok": True, "notion_id": notion_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


## ── Run trigger ───────────────────────────────────────────────────────────────

class TriggerRunRequest(BaseModel):
    project: str = "expansao"
    start_from: str = "ceo"
    context: str = ""
    pipeline: str = "expansao"


@router.post("/runs/trigger")
async def trigger_run(body: TriggerRunRequest, user: str = Depends(_auth)) -> dict:
    """Trigger a pipeline run from the dashboard."""
    import threading  # noqa: PLC0415
    def _do() -> None:
        try:
            from orchestrator.pipeline import run_pipeline  # noqa: PLC0415
            run_pipeline(extra_context=body.context, start_from=body.start_from,
                         project=body.project if body.project not in ("expansao",) else None)
        except Exception as e:
            import logging  # noqa: PLC0415
            logging.getLogger("aios.dashboard").error(f"trigger_run failed: {e}", exc_info=True)
    threading.Thread(target=_do, daemon=True).start()
    return {"triggered": True, "project": body.project, "start_from": body.start_from}


## ── Spec upload ───────────────────────────────────────────────────────────────

@router.post("/spec/upload")
async def upload_spec(
    file: UploadFile = File(...),
    project: str = Form("grc-flow"),
    context: str = Form(""),
    user: str = Depends(_auth),
) -> dict:
    """Upload a spec document and trigger the Spec→PM→Arch→Dev→QA pipeline."""
    import tempfile, os, threading  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    suffix = Path(file.filename or "spec.md").suffix or ".md"
    content = await file.read()

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix,
                                     prefix="aios_spec_", mode="wb") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    def _do() -> None:
        try:
            from orchestrator.pipeline import run_pipeline_from_spec  # noqa: PLC0415
            run_pipeline_from_spec(input_file=tmp_path, project=project,
                                   extra_context=context or file.filename)
        except Exception as e:
            import logging  # noqa: PLC0415
            logging.getLogger("aios.dashboard").error(f"upload_spec pipeline failed: {e}", exc_info=True)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    threading.Thread(target=_do, daemon=True).start()
    return {"triggered": True, "filename": file.filename, "project": project}


@router.get("/gates/pending")
def pending_gates(sprint: str | None = None, project: str | None = None,
                  user: str = Depends(_auth)) -> list[dict]:
    """Return pending gates relevant to a given sprint/project view."""
    from tools.run_tracker import _conn  # noqa: PLC0415
    c = _conn()
    if not c:
        return []
    try:
        import psycopg2.extras  # noqa: PLC0415
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Find pending gates whose run matches the sprint/project context
            query = """
                SELECT gd.run_id, gd.gate_id, gd.decision,
                       pr.project, pr.extra_context, pr.started_at
                FROM gate_decisions gd
                JOIN pipeline_runs pr ON pr.run_id = gd.run_id
                WHERE gd.decision = 'pending'
                  AND pr.status NOT IN ('completed', 'failed')
            """
            params: list = []
            if project:
                query += " AND pr.project = %s"
                params.append(project)
            query += " ORDER BY pr.started_at DESC"
            cur.execute(query, params)
            rows = [dict(r) for r in cur.fetchall()]
        return _serialize(rows)
    except Exception:
        return []
    finally:
        c.close()


@router.get("/stories/{sprint}/{story_id}/deploy-logs")
def get_story_deploy_logs(sprint: str, story_id: str, user: str = Depends(_auth)) -> list[dict]:
    from tools.run_tracker import get_deploy_logs  # noqa: PLC0415
    return _serialize(get_deploy_logs(sprint=sprint, story_id=story_id))


@router.post("/stories/{sprint}/{story_id}/status")
async def update_story_status(sprint: str, story_id: str, body: StoryStatusUpdate,
                               user: str = Depends(_auth)) -> dict:
    from tools.run_tracker import upsert_story  # noqa: PLC0415
    allowed = {"deploy_ready", "deploy_failed", "dev", "qa", "qa_approved", "qa_rejected", "deployed", "done"}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"status inválido: {body.status}")
    upsert_story(sprint=sprint, story_id=story_id, status=body.status)
    await broadcast({"type": "story_update", "sprint": sprint, "story_id": story_id, "status": body.status})
    # Sync Notion backlog item status based on aggregate story statuses
    try:
        from tools.run_tracker import sync_epic_notion_status, get_stories  # noqa: PLC0415
        stories = get_stories(sprint=sprint)
        epic_id = next((s.get("epic_id", "") for s in stories if s.get("story_id") == story_id), "")
        if epic_id:
            import asyncio  # noqa: PLC0415
            asyncio.get_event_loop().run_in_executor(None, sync_epic_notion_status, sprint, epic_id)
    except Exception:
        pass
    return {"ok": True, "sprint": sprint, "story_id": story_id, "status": body.status}


@router.get("/agents/today")
def agents_today(user: str = Depends(_auth)) -> list[dict]:
    from tools.usage_tracker import query_today_agents  # noqa: PLC0415
    return _serialize(query_today_agents())


@router.get("/agents/config")
def agents_config(user: str = Depends(_auth)) -> dict:
    from orchestrator.settings import settings as s  # noqa: PLC0415
    return {
        "expansao": [
            {"agent": "CEO",       "model": s.ceo_model},
            {"agent": "PM",        "model": s.pm_model},
            {"agent": "Architect", "model": s.architect_model},
            {"agent": "Dev",       "model": s.dev_model},
            {"agent": "QA",        "model": s.qa_model},
            {"agent": "DevOps",    "model": s.devops_model},
            {"agent": "Marketing", "model": s.marketing_model},
        ],
        "cwi": [
            {"agent": "Meeting Secretary", "model": s.secretary_model},
            {"agent": "PMO",               "model": s.pmo_model},
            {"agent": "Agile Coach",       "model": s.agile_coach_model},
            {"agent": "Product",           "model": s.product_model},
            {"agent": "Exec Report",       "model": s.exec_report_model},
        ],
    }


@router.get("/credits")
def get_credits(user: str = Depends(_auth)) -> dict:
    from tools.run_tracker import get_credit_summary  # noqa: PLC0415
    return _serialize(get_credit_summary())


@router.get("/credits/topups")
def list_topups(user: str = Depends(_auth)) -> list[dict]:
    from tools.run_tracker import get_topups  # noqa: PLC0415
    return _serialize(get_topups())


class TopupRequest(BaseModel):
    amount_usd: float
    topup_date: str   # YYYY-MM-DD
    notes: str = ""


@router.post("/credits/topups", status_code=201)
def create_topup(req: TopupRequest, user: str = Depends(_auth)) -> dict:
    from tools.run_tracker import add_topup  # noqa: PLC0415
    if req.amount_usd <= 0:
        raise HTTPException(status_code=400, detail="amount_usd deve ser positivo")
    row = add_topup(req.amount_usd, req.topup_date, req.notes)
    if not row:
        raise HTTPException(status_code=500, detail="Erro ao salvar recarga")
    return _serialize(row)


@router.delete("/credits/topups/{topup_id}")
def remove_topup(topup_id: int, user: str = Depends(_auth)) -> dict:
    from tools.run_tracker import delete_topup  # noqa: PLC0415
    delete_topup(topup_id)
    return {"ok": True}


# ── Gate intervention ─────────────────────────────────────────────────────────

class GateDecision(BaseModel):
    decision: str  # "approved" | "rejected"


@router.post("/runs/{run_id}/gate/{gate_id}")
async def decide_gate(run_id: str, gate_id: str, body: GateDecision,
                      user: str = Depends(_auth)) -> dict:
    from tools.run_tracker import set_gate  # noqa: PLC0415
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="decision must be 'approved' or 'rejected'")
    set_gate(run_id, gate_id, body.decision)
    await broadcast({"type": "gate_decision", "run_id": run_id, "gate_id": gate_id, "decision": body.decision})
    return {"ok": True, "run_id": run_id, "gate_id": gate_id, "decision": body.decision}


@router.post("/runs/{run_id}/restart/{stage}")
async def restart_stage(run_id: str, stage: str, user: str = Depends(_auth)) -> dict:
    """Trigger pipeline resume from a specific stage (background task)."""
    from tools.run_tracker import get_run_detail  # noqa: PLC0415
    detail = get_run_detail(run_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Run not found")
    import threading  # noqa: PLC0415
    def _do():
        import os, subprocess, sys  # noqa: PLC0415
        subprocess.Popen([sys.executable, "-m", "orchestrator.cli", "run",
                          "--start-from", stage], cwd=os.getcwd())
    threading.Thread(target=_do, daemon=True).start()
    await broadcast({"type": "restart", "run_id": run_id, "stage": stage})
    return {"ok": True, "run_id": run_id, "restart_from": stage}


# ── SSE stream ────────────────────────────────────────────────────────────────

@router.get("/stream")
async def sse_stream(request: Request) -> StreamingResponse:
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _listeners.append(queue)

    async def event_generator():
        try:
            yield "data: {\"type\": \"connected\"}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            if queue in _listeners:
                _listeners.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Static pages ──────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def dashboard_root():
    from pathlib import Path  # noqa: PLC0415
    p = Path("static/dashboard/index.html")
    return p.read_text(encoding="utf-8") if p.exists() else "<h1>Dashboard not found</h1>"


@router.get("/login-page", response_class=HTMLResponse)
def login_page():
    from pathlib import Path  # noqa: PLC0415
    p = Path("static/dashboard/login.html")
    return p.read_text(encoding="utf-8") if p.exists() else "<h1>Login not found</h1>"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj
