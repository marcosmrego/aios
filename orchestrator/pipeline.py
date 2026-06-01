"""Main pipeline: wires all agents together and enforces human gates."""

from __future__ import annotations

from rich.console import Console

console = Console(legacy_windows=False)


def run_pipeline(extra_context: str = "", start_from: str = "ceo") -> None:
    """
    Run the full AIOS pipeline: CEO -> PM -> Architect -> Dev -> QA -> DevOps -> Marketing.

    Use `start_from` to resume from a specific agent (useful after a rejected gate).
    Outputs from previous stages are loaded from the outputs/ directory when resuming.
    """
    console.rule("[bold green]Expansão AI OS — Pipeline Start[/]")

    ceo_output: dict = {}
    pm_output: dict = {}
    architect_output: dict = {}
    dev_output: dict = {}
    qa_output: dict = {}
    devops_output: dict = {}

    stages = ["ceo", "pm", "architect", "dev", "qa", "devops", "marketing"]
    active_stages = stages[stages.index(start_from):]

    if start_from != "ceo":
        ceo_output, pm_output, architect_output, dev_output, qa_output, devops_output = (
            _load_previous_outputs(start_from)
        )

    # ── Stage 1: CEO ──────────────────────────────────────────────────
    if "ceo" in active_stages:
        from orchestrator.agents.ceo_agent import CEOAgent  # noqa: PLC0415
        ceo_output = CEOAgent().run(extra_context=extra_context)
        if not ceo_output.get("human_approved"):
            console.print("[red bold]Pipeline stopped at CEO->PM gate.[/]")
            return

    # ── Stage 2: PM ───────────────────────────────────────────────────
    if "pm" in active_stages:
        from orchestrator.agents.pm_agent import PMAgent  # noqa: PLC0415
        pm_output = PMAgent().run(ceo_output=ceo_output)
        if not pm_output.get("human_approved"):
            console.print("[red bold]Pipeline stopped at PM->Architect gate.[/]")
            return

    # ── Stage 3: Architect ────────────────────────────────────────────
    if "architect" in active_stages:
        from orchestrator.agents.architect_agent import ArchitectAgent  # noqa: PLC0415
        architect_output = ArchitectAgent().run(pm_output=pm_output)

    # ── Stage 4: Dev ──────────────────────────────────────────────────
    if "dev" in active_stages:
        from orchestrator.agents.dev_agent import DevAgent  # noqa: PLC0415
        dev_output = DevAgent().run(architect_output=architect_output, pm_output=pm_output)

    # ── Stage 5: QA ───────────────────────────────────────────────────
    if "qa" in active_stages:
        from orchestrator.agents.qa_agent import QAAgent  # noqa: PLC0415
        qa_output = QAAgent().run(
            dev_output=dev_output,
            pm_output=pm_output,
            architect_output=architect_output,
        )
        if not qa_output.get("human_approved"):
            console.print("[red bold]Pipeline stopped at QA->Deploy gate.[/]")
            console.print("[dim]Fix the issues and resume with: aios run --start-from dev[/]")
            return

    # ── Stage 6: DevOps ───────────────────────────────────────────────
    if "devops" in active_stages:
        from orchestrator.agents.devops_agent import DevOpsAgent  # noqa: PLC0415
        devops_output = DevOpsAgent().run(
            qa_output=qa_output,
            dev_output=dev_output,
            architect_output=architect_output,
        )

    # ── Stage 7: Marketing ────────────────────────────────────────────
    if "marketing" in active_stages:
        from orchestrator.agents.marketing_agent import MarketingAgent  # noqa: PLC0415
        MarketingAgent().run(devops_output=devops_output, pm_output=pm_output)

    console.rule("[bold green]Pipeline Complete[/]")


def _load_previous_outputs(start_from: str) -> tuple:
    """Load JSON outputs from disk when resuming a pipeline mid-way."""
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    out_dir = Path("outputs/")

    def _latest(prefix: str) -> dict:
        files = sorted(out_dir.glob(f"{prefix}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            console.print(f"[red]No output found for stage '{prefix}'. Run from the beginning.[/]")
            raise SystemExit(1)
        return json.loads(files[0].read_text(encoding="utf-8"))

    stages_before = ["ceo", "pm", "architect", "dev", "qa", "devops"]
    resume_index = ["ceo", "pm", "architect", "dev", "qa", "devops", "marketing"].index(start_from)

    outputs = [_latest(s) if i < resume_index else {} for i, s in enumerate(stages_before)]
    return tuple(outputs)
