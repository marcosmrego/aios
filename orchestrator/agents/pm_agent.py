"""PM Agent — writes PRDs and User Stories from CEO plan."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console

from orchestrator.base_agent import BaseAgent
from orchestrator.settings import settings
from tools.notion import NotionClient
from tools.slack import post_slack_message

console = Console(legacy_windows=False)


class PMAgent(BaseAgent):
    name = "PM Agent"
    role = "Product Manager"
    prompt_file = "agents/prompts/pm.md"

    def __init__(self) -> None:
        self.model = settings.pm_model
        super().__init__()
        self.notion = NotionClient()

    def run(self, ceo_output: dict[str, Any]) -> dict[str, Any]:
        """Generate PRDs from CEO plan -> persist to Notion -> human gate."""
        console.rule("[bold]PM Agent")

        sprint = ceo_output.get("week", "?")
        priorities_json = json.dumps(ceo_output.get("priorities", []), ensure_ascii=False, indent=2)
        pm_instructions = ceo_output.get("pm_instructions", "")

        user_message = f"""
## Output do CEO Agent
**Sprint**: {sprint}

### Prioridades aprovadas
```json
{priorities_json}
```

### Instruções do CEO
{pm_instructions}

Por favor, escreva os PRDs e User Stories para cada prioridade acima.
Inclua o JSON de output ao final da sua resposta.
"""
        response_text = self._run(user_message, max_tokens=8192)
        console.print("\n[dim]--- PM Agent output preview ---[/]")
        console.print(response_text[:800] + ("..." if len(response_text) > 800 else ""))

        output = self._parse_json_output(response_text)

        # Persist output file
        filename = f"pm_prds_{sprint.replace('-', '_').replace('W', 'W')}.json"
        self._save_output(output, filename)

        # Save each PRD to Notion
        for prd in output.get("prds", []):
            self.notion.create_prd_page(sprint, prd)

        # Notify Slack
        if settings.slack_webhook_url and output.get("slack_summary"):
            post_slack_message(f"📝 *PRDs criados — {sprint}*\n{output['slack_summary']}")

        # Human gate: PM output -> Architect
        prd_titles = ", ".join(p.get("title", "?") for p in output.get("prds", []))
        summary = f"PM criou {len(output.get('prds', []))} PRD(s): {prd_titles}"
        approved = self._await_human_approval("pm->architect", summary)
        output["human_approved"] = approved

        if not approved:
            console.print("[red]PRDs rejeitados. Pipeline interrompida.[/]")
        else:
            console.print("[green]PRDs aprovados. Acionando Architect Agent...[/]")

        return output
