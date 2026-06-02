"""CWI Executive Reporting Agent — produces board-level reports from all CWI agent outputs."""

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


class ExecutiveReportingAgent(BaseAgent):
    name = "Executive Reporting Agent"
    role = "Analista de Relatorios Executivos"
    model: str = settings.exec_report_model
    prompt_file = "agents/prompts_cwi/executive_reporting.md"

    def __init__(self) -> None:
        self.model = settings.exec_report_model
        super().__init__()
        self.notion = NotionCWIClient()

    def run(
        self,
        pmo_output: dict[str, Any] | None = None,
        agile_output: dict[str, Any] | None = None,
        extra_context: str = "",
    ) -> dict[str, Any]:
        """Generate executive report for board/directors."""
        console.rule("[bold]Executive Reporting Agent")

        # 1. Load inputs
        pmo_text = json.dumps(pmo_output, ensure_ascii=False, indent=2) if pmo_output else self._load_latest_json("cwi/pmo_")
        agile_text = json.dumps(agile_output, ensure_ascii=False, indent=2) if agile_output else self._load_latest_json("cwi/agile_coach_")

        # 2. Run Claude
        user_message = f"""Gere o relatorio executivo para a diretoria com base nos dados abaixo.

PMO STATUS REPORT:
{pmo_text or "(sem dados de PMO)"}

AGILE COACH REPORT:
{agile_text or "(sem dados de Agile Coach)"}

CONTEXTO ADICIONAL:
{extra_context or "(nenhum)"}

Retorne apenas o JSON, sem texto adicional."""

        response_text = self._run(user_message, max_tokens=8192)

        # 3. Parse output
        output = self._parse_json_output(response_text)

        # 4. Save locally
        today = date.today().strftime("%Y_%m_%d")
        self._save_output(output, f"cwi/executive_report_{today}.json")

        # 5. Human gate before publishing
        summary = f"Relatorio executivo {output.get('periodo', '')} — status geral: {output.get('status_geral', '?')}"
        approved = self._await_human_approval("exec->publish", summary)
        output["human_approved"] = approved

        if not approved:
            console.print("[red]Relatorio rejeitado. Revise e rode novamente.[/]")
            return output

        # 6. Save to Notion
        if settings.cwi_reports_db_id:
            self.notion.create_report_page("Relatorio Executivo", output)

        # 7. Notify Slack
        if settings.slack_webhook_url_cwi and output.get("slack_summary"):
            post_slack_message(f"[EXEC] *Relatorio Executivo {output.get('periodo', '')}*\n{output['slack_summary']}", channel="cwi")

        console.print("[green]Executive Reporting Agent concluido.[/]")
        return output

    @staticmethod
    def _load_latest_json(prefix: str) -> str:
        out_dir = Path("outputs/")
        files = sorted(out_dir.glob(f"{prefix}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0].read_text(encoding="utf-8") if files else ""
