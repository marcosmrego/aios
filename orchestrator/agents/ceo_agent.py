"""CEO Agent — reads Notion backlog and generates the weekly plan."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from rich.console import Console

from orchestrator.base_agent import BaseAgent
from orchestrator.settings import settings
from tools.notion import NotionClient
from tools.slack import post_slack_message

console = Console(legacy_windows=False)


class CEOAgent(BaseAgent):
    name = "CEO Agent"
    role = "Chief Executive Officer"
    prompt_file = "agents/prompts/ceo.md"

    def __init__(self) -> None:
        self.model = settings.ceo_model
        super().__init__()
        self.notion = NotionClient()

    def run(self, extra_context: str = "", force: bool = False) -> dict[str, Any]:
        """Execute the CEO Agent: read backlog -> generate plan -> persist -> gate."""
        console.rule("[bold]CEO Agent")

        week = date.today().strftime("%Y-W%V")

        # 0. Return cached plan if already approved this week (skip expensive Opus call)
        if not force:
            cached = self._load_cached(week)
            if cached:
                console.print(
                    f"[dim]Plano da semana {week} já existe — reutilizando. "
                    f"Use --force para regenerar.[/]"
                )
                cached.setdefault("human_approved", True)
                return cached

        # 1. Fetch backlog from Notion
        console.print("Fetching backlog from Notion...")
        backlog_items = self.notion.get_backlog()
        backlog_text = json.dumps(backlog_items, ensure_ascii=False, indent=2)

        # 2. Build prompt
        user_message = f"""
## Dados do Backlog (Notion)
```json
{backlog_text}
```

## Semana de referência
{week}

## Contexto adicional
{extra_context or "Nenhum contexto adicional."}

Por favor, analise o backlog, defina as prioridades da semana e gere o plano semanal completo
no formato especificado no system prompt. Inclua o JSON de output ao final da sua resposta.
"""
        # 3. Run Claude
        response_text = self._run(user_message, max_tokens=16384)
        console.print("\n[dim]--- CEO Agent output preview ---[/]")
        console.print(response_text[:800] + ("..." if len(response_text) > 800 else ""))

        # 4. Parse structured output
        output = self._parse_json_output(response_text)

        # 5. Persist output
        filename = f"ceo_plan_{week.replace('-', '_')}.json"
        self._save_output(output, filename)

        # 6. Save to Notion (sprint page)
        self.notion.create_sprint_page(week, output)

        # 7. Notify Slack
        if settings.slack_webhook_url_expansao and output.get("slack_summary"):
            post_slack_message(
                f"[CEO] *Plano Semanal CEO — {week}*\n{output['slack_summary']}",
                channel="expansao",
            )

        # 8. Human-in-the-loop gate CEO -> PM
        summary = f"CEO gerou plano para {week}. Prioridades: " + ", ".join(
            p["title"] for p in output.get("priorities", [])
        )
        approved = self._await_human_approval("ceo->pm", summary)
        output["human_approved"] = approved

        if not approved:
            console.print("[red]Plano rejeitado. Pipeline interrompida.[/]")
        else:
            console.print("[green]Plano aprovado. Acionando PM Agent...[/]")

        return output

    @staticmethod
    def _load_cached(week: str) -> dict[str, Any] | None:
        """Return existing CEO output for this week if it exists and is valid."""
        from pathlib import Path  # noqa: PLC0415
        out_dir = Path("outputs/")
        safe = week.replace("-", "_")
        files = sorted(out_dir.glob(f"ceo_plan_{safe}.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return None
        try:
            data = json.loads(files[0].read_text(encoding="utf-8"))
            if data.get("priorities"):  # valid plan has priorities
                return data
        except Exception:
            pass
        return None
