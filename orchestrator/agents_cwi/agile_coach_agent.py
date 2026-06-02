"""CWI Agile Coach Agent — analyzes team metrics and identifies bottlenecks and improvements."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from rich.console import Console

from orchestrator.base_agent import BaseAgent
from orchestrator.settings import settings
from tools.notion_cwi import NotionCWIClient
from tools.slack import post_slack_message

console = Console(legacy_windows=False)


class AgileCoachAgent(BaseAgent):
    name = "Agile Coach Agent"
    role = "Agile Coach"
    model: str = settings.agile_coach_model
    prompt_file = "agents/prompts_cwi/agile_coach.md"

    def __init__(self) -> None:
        self.model = settings.agile_coach_model
        super().__init__()
        self.notion = NotionCWIClient()

    def run(self, metrics_file: str = "", extra_context: str = "") -> dict[str, Any]:
        """Analyze team metrics and produce agile coaching report."""
        console.rule("[bold]Agile Coach Agent")

        # 1. Load metrics
        metrics_text = ""
        if metrics_file:
            metrics_text = Path(metrics_file).read_text(encoding="utf-8")
        else:
            metrics_text = self._load_latest_input("metricas")

        if not metrics_text.strip():
            console.print("[red]Nenhum arquivo de metricas encontrado. Coloque em inputs/cwi/metricas_*.txt[/]")
            raise SystemExit(1)

        # 2. Run Claude
        user_message = f"""Analise as metricas do time abaixo e gere o relatorio de Agile Coach.

METRICAS:
{metrics_text}

CONTEXTO ADICIONAL:
{extra_context or "(nenhum)"}

Retorne apenas o JSON, sem texto adicional."""

        response_text = self._run(user_message, max_tokens=4096)

        # 3. Parse output
        output = self._parse_json_output(response_text)

        # 4. Save locally
        today = date.today().strftime("%Y_%m_%d")
        self._save_output(output, f"cwi/agile_coach_{today}.json")

        # 5. Save to Notion
        if settings.cwi_reports_db_id:
            self.notion.create_report_page("Agile Coach Report", output)

        # 6. Notify Slack
        if settings.slack_webhook_url_cwi and output.get("slack_summary"):
            post_slack_message(f"[AGILE] *Health Score: {output.get('health_score', '?')}/10*\n{output['slack_summary']}", channel="cwi")

        console.print("[green]Agile Coach Agent concluido.[/]")
        return output

    @staticmethod
    def _load_latest_input(prefix: str) -> str:
        input_dir = Path("inputs/cwi/")
        files = sorted(input_dir.glob(f"{prefix}*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0].read_text(encoding="utf-8") if files else ""
