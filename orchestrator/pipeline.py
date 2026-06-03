"""Main pipeline: wires all agents together and enforces human gates."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from rich.console import Console
from tools.run_tracker import (  # noqa: PLC0415
    new_run_id, start_run, update_run, start_stage, complete_stage, skip_stage
)
from orchestrator.dashboard_api import emit_event  # noqa: PLC0415

console = Console(legacy_windows=False)

# Active run context (set at pipeline start)
_current_run_id: str = ""

_STAGES = ["ceo", "pm", "architect", "dev", "qa", "devops", "marketing"]
_STAGE_PREFIXES = {
    "ceo":      "ceo_plan",
    "pm":       "pm_prds",
    "architect": "architect",
    "dev":      "dev",
    "qa":       "qa",
    "devops":   "devops",
}


def run_pipeline(extra_context: str = "", start_from: str = "ceo", force: bool = False) -> None:
    """
    Run the full AIOS pipeline: CEO -> PM -> Architect -> Dev -> QA -> DevOps -> Marketing.

    Auto-detects the optimal resume point from existing outputs unless `force=True`.
    Use `start_from` to manually specify a stage; `force=True` re-runs everything from scratch.
    """
    global _current_run_id
    console.rule("[bold green]Expansão AI OS — Pipeline Start[/]")

    # ── Register run in dashboard DB ──────────────────────────────────────────
    _current_run_id = new_run_id()
    start_run(_current_run_id, project="expansao", pipeline="expansao", extra_context=extra_context)
    emit_event({"type": "run_update", "run_id": _current_run_id, "status": "running"})

    # ── Smart resume: skip stages that already have valid outputs ─────────────
    if start_from == "ceo" and not force:
        detected = _auto_detect_resume()
        if detected != "ceo":
            console.print(
                f"[yellow]Outputs válidos encontrados — retomando a partir de "
                f"[bold]{detected}[/bold] (use --force para reprocessar tudo)[/]"
            )
            start_from = detected

    ceo_output: dict = {}
    pm_output: dict = {}
    architect_output: dict = {}
    dev_output: dict = {}
    qa_output: dict = {}
    devops_output: dict = {}

    active_stages = _STAGES[_STAGES.index(start_from):]

    # Mark skipped stages in dashboard
    for skipped in _STAGES[:_STAGES.index(start_from)]:
        skip_stage(_current_run_id, skipped, reason="output válido reutilizado")

    if start_from != "ceo":
        ceo_output, pm_output, architect_output, dev_output, qa_output, devops_output = (
            _load_previous_outputs(start_from)
        )

    # ── Stage 1: CEO ──────────────────────────────────────────────────
    if "ceo" in active_stages:
        start_stage(_current_run_id, "ceo")
        emit_event({"type": "stage_update", "run_id": _current_run_id, "stage": "ceo", "status": "running"})
        from orchestrator.agents.ceo_agent import CEOAgent  # noqa: PLC0415
        ceo_output = CEOAgent().run(extra_context=extra_context, force=force)
        _stage_done(_current_run_id, "ceo", ceo_output)
        if not ceo_output.get("human_approved"):
            console.print("[red bold]Pipeline stopped at CEO->PM gate.[/]")
            update_run(_current_run_id, "paused", error_msg="Gate CEO->PM rejeitado")
            return

    # ── Stage 2: PM ───────────────────────────────────────────────────
    if "pm" in active_stages:
        start_stage(_current_run_id, "pm")
        emit_event({"type": "stage_update", "run_id": _current_run_id, "stage": "pm", "status": "running"})
        from orchestrator.agents.pm_agent import PMAgent  # noqa: PLC0415
        pm_output = PMAgent().run(ceo_output=ceo_output)
        _stage_done(_current_run_id, "pm", pm_output)
        if not pm_output.get("human_approved"):
            console.print("[red bold]Pipeline stopped at PM->Architect gate.[/]")
            update_run(_current_run_id, "paused", error_msg="Gate PM->Architect rejeitado")
            return

    # ── Stage 3: Architect ────────────────────────────────────────────
    if "architect" in active_stages:
        start_stage(_current_run_id, "architect")
        emit_event({"type": "stage_update", "run_id": _current_run_id, "stage": "architect", "status": "running"})
        from orchestrator.agents.architect_agent import ArchitectAgent  # noqa: PLC0415
        architect_output = ArchitectAgent().run(pm_output=pm_output)
        _stage_done(_current_run_id, "architect", architect_output)

    # ── Stage 4: Dev ──────────────────────────────────────────────────
    if "dev" in active_stages:
        start_stage(_current_run_id, "dev")
        emit_event({"type": "stage_update", "run_id": _current_run_id, "stage": "dev", "status": "running"})
        from orchestrator.agents.dev_agent import DevAgent  # noqa: PLC0415
        dev_output = DevAgent().run(architect_output=architect_output, pm_output=pm_output)
        n_impls = len(dev_output.get("implementations", []))
        _stage_done(_current_run_id, "dev", dev_output, summary=f"{n_impls} stories implementadas")

    # ── Stage 5: QA (with auto-retry loop for empty implementations) ─────────
    if "qa" in active_stages:
        start_stage(_current_run_id, "qa")
        emit_event({"type": "stage_update", "run_id": _current_run_id, "stage": "qa", "status": "running"})
        from orchestrator.agents.qa_agent import QAAgent  # noqa: PLC0415
        from orchestrator.agents.dev_agent import DevAgent  # noqa: PLC0415

        _MAX_DEV_RETRIES = 2
        for _attempt in range(_MAX_DEV_RETRIES + 1):
            qa_output = QAAgent().run(
                dev_output=dev_output,
                pm_output=pm_output,
                architect_output=architect_output,
            )

            # Identify stories with zero files delivered
            empty_stories = [
                impl["story_id"]
                for impl in dev_output.get("implementations", [])
                if not impl.get("files_created") and impl.get("story_id")
            ]

            if not empty_stories or _attempt == _MAX_DEV_RETRIES:
                break

            console.print(
                f"[yellow]QA detectou {len(empty_stories)} stories sem arquivos "
                f"— re-executando Dev (tentativa {_attempt + 1}/{_MAX_DEV_RETRIES})...[/]"
            )
            dev_output = DevAgent().run(
                architect_output=architect_output,
                pm_output=pm_output,
                retry_story_ids=empty_stories,
            )

        _stage_done(_current_run_id, "qa", qa_output,
                    summary=f"{'APROVADO' if qa_output.get('approved') else 'REPROVADO'} — {qa_output.get('overall_notes','')}")
        if not qa_output.get("human_approved"):
            console.print("[red bold]Pipeline stopped at QA->Deploy gate.[/]")
            console.print("[dim]Fix the issues and resume with: aios run --start-from dev[/]")
            update_run(_current_run_id, "paused", error_msg="Gate QA->Deploy rejeitado")
            return

    # ── Stage 6: DevOps ───────────────────────────────────────────────
    if "devops" in active_stages:
        start_stage(_current_run_id, "devops")
        emit_event({"type": "stage_update", "run_id": _current_run_id, "stage": "devops", "status": "running"})
        from orchestrator.agents.devops_agent import DevOpsAgent  # noqa: PLC0415
        devops_output = DevOpsAgent().run(
            qa_output=qa_output,
            dev_output=dev_output,
            architect_output=architect_output,
        )
        _stage_done(_current_run_id, "devops", devops_output)

    # ── Stage 7: Marketing ────────────────────────────────────────────
    if "marketing" in active_stages:
        start_stage(_current_run_id, "marketing")
        emit_event({"type": "stage_update", "run_id": _current_run_id, "stage": "marketing", "status": "running"})
        from orchestrator.agents.marketing_agent import MarketingAgent  # noqa: PLC0415
        MarketingAgent().run(devops_output=devops_output, pm_output=pm_output)
        complete_stage(_current_run_id, "marketing", status="completed")

    update_run(_current_run_id, "completed")
    emit_event({"type": "run_update", "run_id": _current_run_id, "status": "completed"})
    console.rule("[bold green]Pipeline Complete[/]")


def _stage_done(run_id: str, stage: str, output: dict, summary: str = "") -> None:
    cost = output.get("_stage_cost", 0.0)
    complete_stage(run_id, stage, status="completed", cost_usd=cost, output_summary=summary or str(output)[:200])
    emit_event({"type": "stage_update", "run_id": run_id, "stage": stage, "status": "completed"})


def _auto_detect_resume() -> str:
    """Detect the first stage that lacks a valid output for the current week."""
    week = date.today().strftime("%Y-W%V")
    out_dir = Path("outputs/")

    for stage in _STAGES[:-1]:  # marketing has no output file to check
        prefix = _STAGE_PREFIXES[stage]
        files = sorted(out_dir.glob(f"{prefix}*{week.replace('-W', '*W')}*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            # Also try with underscore variants (2026_W23)
            safe = week.replace("-", "_")
            files = sorted(out_dir.glob(f"{prefix}*{safe}*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return stage

        # Dev: check that at least one story has files
        if stage == "dev":
            try:
                data = json.loads(files[0].read_text(encoding="utf-8"))
                has_code = any(
                    impl.get("files_created")
                    for impl in data.get("implementations", [])
                )
                if not has_code:
                    return stage
            except Exception:
                return stage

        # QA: if output exists but not approved, resume from dev (not devops)
        if stage == "qa":
            try:
                data = json.loads(files[0].read_text(encoding="utf-8"))
                if not data.get("approved"):
                    return "dev"
            except Exception:
                return stage

    return "marketing"


def _load_previous_outputs(start_from: str) -> tuple[Any, ...]:
    """Load JSON outputs from disk when resuming a pipeline mid-way."""
    out_dir = Path("outputs/")

    def _latest(prefix: str) -> dict:
        files = sorted(out_dir.glob(f"{prefix}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            console.print(f"[red]No output found for stage '{prefix}'. Run from the beginning.[/]")
            raise SystemExit(1)
        data = json.loads(files[0].read_text(encoding="utf-8"))
        console.print(f"[dim]Loaded: {files[0].name}[/]")
        return data

    stages_before = ["ceo", "pm", "architect", "dev", "qa", "devops"]
    resume_index = _STAGES.index(start_from)

    outputs = [_latest(_STAGE_PREFIXES[s]) if i < resume_index else {} for i, s in enumerate(stages_before)]
    return tuple(outputs)
