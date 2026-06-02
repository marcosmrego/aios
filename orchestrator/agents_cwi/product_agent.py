"""CWI Product Agent — transforms raw demands into structured epics, stories and acceptance criteria."""

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


class ProductAgent(BaseAgent):
    name = "Product Agent"
    role = "Product Manager CWI"
    model: str = settings.product_model
    prompt_file = "agents/prompts_cwi/product.md"

    def __init__(self) -> None:
        self.model = settings.product_model
        super().__init__()
        self.notion = NotionCWIClient()

    def run(self, demands_file: str = "", demands_text: str = "") -> dict[str, Any]:
        """Transform raw demands into epics, stories, and acceptance criteria."""
        console.rule("[bold]Product Agent")

        # 1. Load demands
        if not demands_text and demands_file:
            demands_text = Path(demands_file).read_text(encoding="utf-8")
        elif not demands_text:
            demands_text = self._load_latest_input("demandas")

        if not demands_text.strip():
            console.print("[red]Nenhuma demanda encontrada. Coloque em inputs/cwi/demandas_*.txt[/]")
            raise SystemExit(1)

        # 2. Run Claude
        user_message = f"""Analise as demandas abaixo e gere epicos, historias e criterios de aceite.

DEMANDAS:
{demands_text}

Retorne apenas o JSON, sem texto adicional."""

        response_text = self._run(user_message, max_tokens=8192)

        # 3. Parse output
        output = self._parse_json_output(response_text)

        # 4. Save locally
        today = date.today().strftime("%Y_%m_%d")
        self._save_output(output, f"cwi/product_{today}.json")

        # 5. Human gate before saving to backlog
        summary = f"Product Agent mapeou {len(output.get('epicos', []))} epico(s) com historias."
        approved = self._await_human_approval("product->backlog", summary)
        output["human_approved"] = approved

        if not approved:
            console.print("[red]Backlog rejeitado. Revise e rode novamente.[/]")
            return output

        # 6. Save to Notion backlog
        if settings.cwi_backlog_db_id:
            for epic in output.get("epicos", []):
                self.notion.create_backlog_epic(epic)

        # 7. Notify Slack
        if settings.slack_webhook_url_cwi and output.get("slack_summary"):
            post_slack_message(f"[PRODUTO] *Backlog atualizado*\n{output['slack_summary']}", channel="cwi")

        console.print("[green]Product Agent concluido.[/]")
        return output

    @staticmethod
    def _load_latest_input(prefix: str) -> str:
        input_dir = Path("inputs/cwi/")
        files = sorted(input_dir.glob(f"{prefix}*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0].read_text(encoding="utf-8") if files else ""
