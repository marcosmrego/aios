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
    pipeline = "cwi"
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

        # 2. Pull dashboard data (pipeline reality vs planned)
        dashboard_text = self._load_dashboard_data()

        # 3. Run Claude
        user_message = f"""Gere o relatorio executivo para a diretoria com base nos dados abaixo.

PMO STATUS REPORT:
{pmo_text or "(sem dados de PMO)"}

AGILE COACH REPORT:
{agile_text or "(sem dados de Agile Coach)"}

DADOS DO PIPELINE AIOS (fonte de verdade — o que foi realmente executado):
{dashboard_text}

CONTEXTO ADICIONAL:
{extra_context or "(nenhum)"}

Use os dados do pipeline AIOS para conciliar o que foi planejado vs o que foi realmente entregue.
Destaque discrepâncias entre o planejado (Notion) e o executado (pipeline).
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

    def _load_dashboard_data(self) -> str:
        """Pull story status, costs and recent runs from the dashboard DB."""
        try:
            from tools.run_tracker import get_stories, get_cost_summary, get_runs  # noqa: PLC0415
            from datetime import date  # noqa: PLC0415

            sprint = date.today().strftime("%Y-W%V")
            stories = get_stories(sprint=sprint)
            costs = get_cost_summary()
            runs = get_runs(limit=10)

            # Summarise by project/epic
            by_epic: dict = {}
            for s in stories:
                key = f"{s['project']} / {s.get('epic_id', '?')}"
                by_epic.setdefault(key, {"total": 0, "dev": 0, "qa_approved": 0, "qa_rejected": 0, "backlog": 0})
                by_epic[key]["total"] += 1
                by_epic[key][s.get("status", "backlog")] = by_epic[key].get(s.get("status", "backlog"), 0) + 1

            summary = {
                "sprint": sprint,
                "stories_por_epico": by_epic,
                "custos": costs.get("totals", {}),
                "ultimas_execucoes": [
                    {"run_id": r["run_id"], "status": r["status"],
                     "cost": float(r.get("cost_usd", 0)), "project": r["project"]}
                    for r in runs[:5]
                ],
            }
            return json.dumps(summary, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            return f"(dashboard indisponível: {e})"

    @staticmethod
    def _load_latest_json(prefix: str) -> str:
        out_dir = Path("outputs/")
        files = sorted(out_dir.glob(f"{prefix}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0].read_text(encoding="utf-8") if files else ""
