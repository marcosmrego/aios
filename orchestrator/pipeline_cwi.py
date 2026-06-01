"""CWI pipeline — wires all CWI agents together."""

from __future__ import annotations

from rich.console import Console

console = Console(legacy_windows=False)


def run_pipeline_cwi(
    extra_context: str = "",
    start_from: str = "meeting_secretary",
    input_file: str = "",
) -> None:
    """
    Run the CWI pipeline: Meeting Secretary -> PMO -> Agile Coach -> Product -> Executive Reporting.

    Use `start_from` to run from a specific agent.
    Use `input_file` to pass a transcript or metrics file to the first agent.
    """
    console.rule("[bold green]CWI Pipeline Start[/]")

    meeting_output: dict = {}
    pmo_output: dict = {}
    agile_output: dict = {}

    stages = ["meeting_secretary", "pmo", "agile_coach", "product", "executive_reporting"]
    active_stages = stages[stages.index(start_from):]

    # ── Stage 1: Meeting Secretary ────────────────────────────────────────────
    if "meeting_secretary" in active_stages:
        from orchestrator.agents_cwi.meeting_secretary_agent import MeetingSecretaryAgent  # noqa: PLC0415
        meeting_output = MeetingSecretaryAgent().run(transcript_file=input_file)

    # ── Stage 2: PMO ──────────────────────────────────────────────────────────
    if "pmo" in active_stages:
        from orchestrator.agents_cwi.pmo_agent import PMOAgent  # noqa: PLC0415
        pmo_output = PMOAgent().run(meeting_output=meeting_output, extra_context=extra_context)

    # ── Stage 3: Agile Coach ──────────────────────────────────────────────────
    if "agile_coach" in active_stages:
        from orchestrator.agents_cwi.agile_coach_agent import AgileCoachAgent  # noqa: PLC0415
        agile_output = AgileCoachAgent().run(extra_context=extra_context)

    # ── Stage 4: Product ──────────────────────────────────────────────────────
    if "product" in active_stages:
        from orchestrator.agents_cwi.product_agent import ProductAgent  # noqa: PLC0415
        product_output = ProductAgent().run()
        if not product_output.get("human_approved"):
            console.print("[red bold]Pipeline CWI interrompido no gate product->backlog.[/]")
            return

    # ── Stage 5: Executive Reporting ──────────────────────────────────────────
    if "executive_reporting" in active_stages:
        from orchestrator.agents_cwi.executive_reporting_agent import ExecutiveReportingAgent  # noqa: PLC0415
        exec_output = ExecutiveReportingAgent().run(pmo_output=pmo_output, agile_output=agile_output)
        if not exec_output.get("human_approved"):
            console.print("[red bold]Pipeline CWI interrompido no gate exec->publish.[/]")
            return

    console.rule("[bold green]CWI Pipeline Completo[/]")
