"""Architect Agent — turns PRDs into technical architecture decisions."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console

from orchestrator.base_agent import BaseAgent
from orchestrator.settings import settings
from tools.notion import NotionClient
from tools.slack import post_slack_message

console = Console(legacy_windows=False)


class ArchitectAgent(BaseAgent):
    name = "Architect Agent"
    role = "Software Architect"
    prompt_file = "agents/prompts/architect.md"

    def __init__(self) -> None:
        self.model = settings.architect_model
        super().__init__()
        self.notion = NotionClient()

    def run(self, pm_output: dict[str, Any]) -> dict[str, Any]:
        """Generate architecture docs from PRDs → persist → pass to Dev Agent."""
        console.rule("[bold]Architect Agent")

        sprint = pm_output.get("sprint", "?")
        prds_json = json.dumps(pm_output.get("prds", []), ensure_ascii=False, indent=2)

        user_message = f"""
## Output do PM Agent
**Sprint**: {sprint}

### PRDs e User Stories
```json
{prds_json}
```

Analise cada PRD, tome as decisões de arquitetura técnica e gere a documentação completa
no formato especificado. Inclua o JSON de output ao final da sua resposta.
"""
        response_text = self._run(user_message, max_tokens=8192)
        console.print("\n[dim]--- Architect Agent output preview ---[/]")
        console.print(response_text[:800] + ("..." if len(response_text) > 800 else ""))

        output = self._parse_json_output(response_text)

        filename = f"architect_{sprint.replace('-', '_')}.json"
        self._save_output(output, filename)

        # Persist each architecture doc to Notion as a page inside the PRD
        for arch in output.get("architectures", []):
            self.notion.create_architecture_page(sprint, arch)

        if settings.slack_webhook_url and output.get("slack_summary"):
            post_slack_message(f"🏗️ *Arquitetura definida — {sprint}*\n{output['slack_summary']}")

        console.print("[green]Arquitetura documentada. Acionando Dev Agent...[/]")
        return output
