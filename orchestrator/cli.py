"""CLI entry point: `aios` command."""

import typer
from rich.console import Console

app = typer.Typer(name="aios", help="Expansao AI OS -- Multi-agent orchestrator")
console = Console(legacy_windows=False)

_AGENTS_EXPANSAO = ["ceo", "pm", "architect", "dev", "qa", "devops", "marketing", "all"]
_AGENTS_CWI = ["meeting-secretary", "pmo", "agile-coach", "product", "executive-reporting", "all"]
_PIPELINES = ["expansao", "cwi"]


@app.command()
def run(
    context: str = typer.Option("", "--context", "-c", help="Contexto adicional para o agente"),
    agent: str = typer.Option("all", "--agent", "-a", help="Agente especifico a executar"),
    pipeline: str = typer.Option("expansao", "--pipeline", "-p", help="Pipeline: expansao | cwi"),
    start_from: str = typer.Option("", "--start-from", "-s", help="Retomar pipeline a partir de um agente"),
    input_file: str = typer.Option("", "--input", "-i", help="(CWI) Arquivo de input para o agente"),
) -> None:
    """Run a pipeline or a specific agent. Use --pipeline to select: expansao (default) or cwi."""
    if pipeline not in _PIPELINES:
        console.print(f"[red]Pipeline desconhecido: {pipeline}. Opcoes: {', '.join(_PIPELINES)}[/]")
        raise typer.Exit(1)

    if pipeline == "cwi":
        _run_cwi_command(agent, context, start_from or "meeting_secretary", input_file)
        return

    # ── Expansao AI pipeline ──────────────────────────────────────────────────
    if agent != "all" and agent not in _AGENTS_EXPANSAO:
        console.print(f"[red]Agente desconhecido: {agent}. Opcoes: {', '.join(_AGENTS_EXPANSAO)}[/]")
        raise typer.Exit(1)

    if agent == "all":
        from orchestrator.pipeline import run_pipeline  # noqa: PLC0415
        run_pipeline(extra_context=context, start_from=start_from or "ceo")
        return

    agent_map = {
        "ceo": _run_ceo,
        "pm": _run_pm,
        "architect": _run_architect,
        "dev": _run_dev,
        "qa": _run_qa,
        "devops": _run_devops,
        "marketing": _run_marketing,
    }
    agent_map[agent](context)


def _run_cwi_command(agent: str, context: str, start_from: str, input_file: str) -> None:
    """Dispatch CWI agent or full CWI pipeline."""
    if agent == "all":
        from orchestrator.pipeline_cwi import run_pipeline_cwi  # noqa: PLC0415
        run_pipeline_cwi(extra_context=context, start_from=start_from, input_file=input_file)
        return

    agent_map_cwi = {
        "meeting-secretary": lambda: _run_meeting_secretary(input_file, context),
        "pmo": lambda: _run_pmo(context),
        "agile-coach": lambda: _run_agile_coach(input_file, context),
        "product": lambda: _run_product(input_file),
        "executive-reporting": lambda: _run_executive_reporting(context),
    }

    if agent not in agent_map_cwi:
        console.print(f"[red]Agente CWI desconhecido: {agent}. Opcoes: {', '.join(_AGENTS_CWI)}[/]")
        raise typer.Exit(1)

    agent_map_cwi[agent]()


@app.command()
def status(
    pipeline: str = typer.Option("expansao", "--pipeline", "-p", help="Pipeline: expansao | cwi"),
) -> None:
    """Show the last outputs for each pipeline stage."""
    if pipeline == "cwi":
        _status_cwi()
        return
    _status_expansao()


def _status_expansao() -> None:
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    output_dir = Path("outputs/")
    if not output_dir.exists():
        console.print("[yellow]No outputs yet. Run `aios run` first.[/]")
        return

    stage_prefixes = ["ceo_plan", "pm_prds", "architect", "dev", "qa", "devops", "marketing"]
    console.rule("Expansao AI — Pipeline Status")
    for prefix in stage_prefixes:
        files = sorted(output_dir.glob(f"{prefix}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            console.print(f"  [dim]{prefix:15}[/] — no output")
            continue
        data = json.loads(files[0].read_text(encoding="utf-8"))
        week = data.get("week") or data.get("sprint", "?")
        approved = data.get("human_approved")
        approved_str = " [OK]" if approved else (" [FAIL]" if approved is False else "")
        console.print(f"  [bold]{prefix:15}[/]  week={week}{approved_str}  ({files[0].name})")


def _status_cwi() -> None:
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    out = Path("outputs/cwi/")
    console.rule("CWI — Pipeline Status")

    stages = {
        "Meeting Secretary": "meeting_",
        "PMO":               "pmo_",
        "Agile Coach":       "agile_coach_",
        "Product":           "product_",
        "Executive Report":  "executive_report_",
        "Task Digest":       "digest_",
    }

    for label, prefix in stages.items():
        files = sorted(out.glob(f"{prefix}*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if out.exists() else []
        if not files:
            console.print(f"  [dim]{label:20}[/] — sem output")
            continue
        data = json.loads(files[0].read_text(encoding="utf-8"))
        info = data.get("periodo") or data.get("data") or data.get("titulo", "?")
        console.print(f"  [bold]{label:20}[/]  {info}  ({files[0].name})")

    # Watcher status
    console.print()
    try:
        import subprocess  # noqa: PLC0415
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", "ExpansaoAIOS_Watch", "/FO", "LIST"],
            capture_output=True, text=True
        )
        for line in r.stdout.splitlines():
            if "Status" in line or "Proximo" in line or "Ultimo" in line:
                console.print(f"  [dim]Watcher: {line.strip()}[/]")
    except Exception:
        pass


# ── Single-agent runners ──────────────────────────────────────────────────────

def _run_ceo(context: str) -> None:
    from orchestrator.agents.ceo_agent import CEOAgent  # noqa: PLC0415
    CEOAgent().run(extra_context=context)


def _run_pm(context: str) -> None:
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    from orchestrator.agents.pm_agent import PMAgent  # noqa: PLC0415

    ceo_files = sorted(Path("outputs/").glob("ceo_plan_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not ceo_files:
        console.print("[red]No CEO output found. Run `aios run --agent ceo` first.[/]")
        raise typer.Exit(1)
    ceo_output = json.loads(ceo_files[0].read_text(encoding="utf-8"))
    PMAgent().run(ceo_output=ceo_output)


def _run_architect(context: str) -> None:
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    from orchestrator.agents.architect_agent import ArchitectAgent  # noqa: PLC0415

    pm_files = sorted(Path("outputs/").glob("pm_prds_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pm_files:
        console.print("[red]No PM output found. Run `aios run --agent pm` first.[/]")
        raise typer.Exit(1)
    pm_output = json.loads(pm_files[0].read_text(encoding="utf-8"))
    ArchitectAgent().run(pm_output=pm_output)


def _run_dev(context: str) -> None:
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    from orchestrator.agents.dev_agent import DevAgent  # noqa: PLC0415

    pm_files = sorted(Path("outputs/").glob("pm_prds_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    arch_files = sorted(Path("outputs/").glob("architect_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pm_files or not arch_files:
        console.print("[red]Missing PM or Architect outputs.[/]")
        raise typer.Exit(1)
    pm_output = json.loads(pm_files[0].read_text(encoding="utf-8"))
    architect_output = json.loads(arch_files[0].read_text(encoding="utf-8"))
    DevAgent().run(architect_output=architect_output, pm_output=pm_output)


def _run_qa(context: str) -> None:
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    from orchestrator.agents.qa_agent import QAAgent  # noqa: PLC0415

    def _load(prefix: str) -> dict:
        files = sorted(Path("outputs/").glob(f"{prefix}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            console.print(f"[red]Missing output for: {prefix}[/]")
            raise typer.Exit(1)
        return json.loads(files[0].read_text(encoding="utf-8"))

    QAAgent().run(
        dev_output=_load("dev"),
        pm_output=_load("pm_prds"),
        architect_output=_load("architect"),
    )


def _run_devops(context: str) -> None:
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    from orchestrator.agents.devops_agent import DevOpsAgent  # noqa: PLC0415

    def _load(prefix: str) -> dict:
        files = sorted(Path("outputs/").glob(f"{prefix}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            console.print(f"[red]Missing output for: {prefix}[/]")
            raise typer.Exit(1)
        return json.loads(files[0].read_text(encoding="utf-8"))

    DevOpsAgent().run(
        qa_output=_load("qa"),
        dev_output=_load("dev"),
        architect_output=_load("architect"),
    )


def _run_marketing(context: str) -> None:
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    from orchestrator.agents.marketing_agent import MarketingAgent  # noqa: PLC0415

    def _load(prefix: str) -> dict:
        files = sorted(Path("outputs/").glob(f"{prefix}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            console.print(f"[red]Missing output for: {prefix}[/]")
            raise typer.Exit(1)
        return json.loads(files[0].read_text(encoding="utf-8"))

    MarketingAgent().run(devops_output=_load("devops"), pm_output=_load("pm_prds"))


# ── CWI single-agent runners ─────────────────────────────────────────────────

def _run_meeting_secretary(input_file: str, context: str) -> None:
    from orchestrator.agents_cwi.meeting_secretary_agent import MeetingSecretaryAgent  # noqa: PLC0415
    MeetingSecretaryAgent().run(transcript_file=input_file)


def _run_pmo(context: str) -> None:
    from orchestrator.agents_cwi.pmo_agent import PMOAgent  # noqa: PLC0415
    PMOAgent().run(extra_context=context)


def _run_agile_coach(input_file: str, context: str) -> None:
    from orchestrator.agents_cwi.agile_coach_agent import AgileCoachAgent  # noqa: PLC0415
    AgileCoachAgent().run(metrics_file=input_file, extra_context=context)


def _run_product(input_file: str) -> None:
    from orchestrator.agents_cwi.product_agent import ProductAgent  # noqa: PLC0415
    ProductAgent().run(demands_file=input_file)


def _run_executive_reporting(context: str) -> None:
    from orchestrator.agents_cwi.executive_reporting_agent import ExecutiveReportingAgent  # noqa: PLC0415
    ExecutiveReportingAgent().run(extra_context=context)


@app.command()
def digest() -> None:
    """Gera digest de todas as tarefas pendentes no CWI Meetings e salva no CWI Reports."""
    from orchestrator.agents_cwi.task_digest_agent import TaskDigestAgent  # noqa: PLC0415
    result = TaskDigestAgent().run(triggered_by="manual")
    console.print(f"\nTotal: {result.get('total_acoes', 0)} acoes  |  Alta: {result.get('alta', 0)}")


@app.command()
def importar(
    query: str = typer.Option("reuniao", "--query", "-q", help="Termo de busca no Notion (default: reuniao)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Apenas lista o que seria importado, sem criar nada"),
) -> None:
    """Importa paginas de reunioes existentes do Notion para o CWI Meetings database."""
    from orchestrator.importador import importar_reunioes  # noqa: PLC0415
    importar_reunioes(dry_run=dry_run, query=query)


@app.command()
def watch(
    interval: int = typer.Option(0, "--interval", "-i", help="Intervalo em segundos entre verificacoes (default: 120)"),
) -> None:
    """Watch Notion for new meeting transcriptions and process them automatically."""
    from orchestrator.watcher import watch_cwi_meetings  # noqa: PLC0415
    watch_cwi_meetings(interval_seconds=interval)


if __name__ == "__main__":
    app()
