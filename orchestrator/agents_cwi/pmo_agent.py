"""CWI PMO Agent — consolidates weekly meeting minutes into executive status reports."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from rich.console import Console

from orchestrator.base_agent import BaseAgent
from orchestrator.settings import settings
from tools.notion_cwi import NotionCWIClient
from tools.slack import post_slack_message

console = Console(legacy_windows=False)


class PMOAgent(BaseAgent):
    name = "PMO Agent"
    role = "Project Management Officer"
    model: str = settings.pmo_model
    prompt_file = "agents/prompts_cwi/pmo.md"

    def __init__(self) -> None:
        self.model = settings.pmo_model
        super().__init__()
        self.notion = NotionCWIClient()

    def run(
        self,
        meeting_output: dict[str, Any] | None = None,
        indicators_file: str = "",
        extra_context: str = "",
        days: int = 7,
    ) -> dict[str, Any]:
        """
        Generate weekly PMO status report.

        Input priority:
        - meeting_output: dict passado diretamente (pipeline sequencial)
        - CWI Meetings database: busca automatica dos ultimos `days` dias
        - outputs/cwi/meeting_*.json: fallback para ultimo arquivo local
        """
        console.rule("[bold]PMO Agent")

        # 1. Build meetings context
        meeting_text = self._build_meeting_context(meeting_output, days)

        # 2. Build indicators context
        indicators_text = ""
        if indicators_file:
            indicators_text = Path(indicators_file).read_text(encoding="utf-8")
        else:
            indicators_text = self._load_latest_input("indicadores")

        # 3. Determine period
        today = date.today()
        iso_week = today.strftime("%Y-W%V")
        console.print(f"[dim]Periodo: {iso_week} | Reunioes encontradas: {meeting_text.count('=== REUNIAO')}[/]")

        # 4. Run Claude
        user_message = f"""Gere o status report PMO semanal com base nas informacoes abaixo.

PERIODO: {iso_week}

ATAS DAS REUNIOES DA SEMANA:
{meeting_text or "(nenhuma reuniao registrada esta semana)"}

INDICADORES DO PERIODO:
{indicators_text or "(sem indicadores disponiveis)"}

CONTEXTO ADICIONAL:
{extra_context or "(nenhum)"}

Retorne apenas o JSON, sem texto adicional."""

        response_text = self._run(user_message, max_tokens=8192)

        # 5. Parse output
        output = self._parse_json_output(response_text)
        output.setdefault("periodo", iso_week)

        # 6. Save locally
        self._save_output(output, f"cwi/pmo_{today.strftime('%Y_%m_%d')}.json")

        # 7. Save to Notion CWI Reports
        if settings.cwi_reports_db_id:
            self.notion.create_report_page("PMO Status Report", output)

        # 8. Notify Slack
        if settings.slack_webhook_url_cwi and output.get("slack_summary"):
            post_slack_message(
                f"[PMO] *Status Report {output.get('periodo', iso_week)}*\n{output['slack_summary']}",
                channel="cwi",
            )

        console.print("[green]PMO Agent concluido.[/]")
        return output

    def _build_meeting_context(self, meeting_output: dict[str, Any] | None, days: int) -> str:
        """Build consolidated meeting text from Notion or fallback sources."""
        # Priority 1: passed directly (sequential pipeline)
        if meeting_output:
            return json.dumps(meeting_output, ensure_ascii=False, indent=2)

        # Priority 2: fetch from CWI Meetings database
        if settings.cwi_meetings_db_id:
            try:
                meetings = self.notion.get_weekly_meetings(days=days)
                if meetings:
                    console.print(f"[dim]Carregadas {len(meetings)} reunioes do Notion[/]")
                    parts = []
                    for m in meetings:
                        parts.append(
                            f"=== REUNIAO: {m['titulo']} ({m['data']}) ===\n{m['conteudo']}"
                        )
                    return "\n\n".join(parts)
            except Exception as e:
                console.print(f"[yellow]Aviso: nao foi possivel buscar reunioes do Notion: {e}[/]")

        # Priority 3: latest local JSON
        latest = self._load_latest_json("cwi/meeting_")
        if latest:
            console.print("[dim]Usando ultimo arquivo local de reuniao[/]")
        return latest

    @staticmethod
    def _load_latest_input(prefix: str) -> str:
        input_dir = Path("inputs/cwi/")
        files = sorted(input_dir.glob(f"{prefix}*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0].read_text(encoding="utf-8") if files else ""

    @staticmethod
    def _load_latest_json(prefix: str) -> str:
        out_dir = Path("outputs/")
        files = sorted(out_dir.glob(f"{prefix}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0].read_text(encoding="utf-8") if files else ""
