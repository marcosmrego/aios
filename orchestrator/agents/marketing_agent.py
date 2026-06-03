"""Marketing Agent — creates LinkedIn and Threads content from successful deploys."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console

from orchestrator.base_agent import BaseAgent
from orchestrator.settings import settings
from tools.notion import NotionClient
from tools.slack import post_slack_message

console = Console(legacy_windows=False)


class MarketingAgent(BaseAgent):
    name = "Marketing Agent"
    role = "Content Marketing Manager"
    prompt_file = "agents/prompts/marketing.md"

    def __init__(self) -> None:
        self.model = settings.marketing_model
        super().__init__()
        self.notion = NotionClient()

    def run(
        self,
        devops_output: dict[str, Any],
        pm_output: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate marketing content from successful deploys."""
        console.rule("[bold]Marketing Agent")

        if not devops_output.get("deploy_success"):
            console.print("[yellow]Deploy não foi bem-sucedido. Pulando Marketing Agent.[/]")
            return {"sprint": devops_output.get("sprint"), "content_pieces": [], "skipped": True}

        sprint = devops_output.get("sprint") or pm_output.get("sprint", "unknown")
        deploys_json = json.dumps(devops_output.get("deploys", []), ensure_ascii=False, indent=2)
        prds_json = json.dumps(
            [{"title": p["title"], "stories": p.get("stories", [])} for p in pm_output.get("prds", [])],
            ensure_ascii=False,
            indent=2,
        )

        user_message = f"""
## Sprint
{sprint}

## Deploys realizados com sucesso
```json
{deploys_json}
```

## Funcionalidades lançadas (PRDs)
```json
{prds_json}
```

Crie o conteúdo de marketing para LinkedIn e Threads para cada funcionalidade lançada.
Siga o tom de voz da Expansão AI descrito no system prompt.
Inclua o JSON de output ao final da sua resposta.
"""
        response_text = self._run(user_message, max_tokens=16384)
        console.print("\n[dim]--- Marketing Agent output preview ---[/]")
        console.print(response_text[:600] + ("..." if len(response_text) > 600 else ""))

        output = self._parse_json_output(response_text)

        safe_sprint = "".join(c if c.isalnum() or c in "-_" else "_" for c in sprint)
        filename = f"marketing_{safe_sprint}.json"
        self._save_output(output, filename)

        # Persist content to Notion for review
        for piece in output.get("content_pieces", []):
            self.notion.create_content_page(sprint, piece)

        if settings.slack_webhook_url_expansao and output.get("slack_summary"):
            post_slack_message(
                f"📣 *Conteúdo criado para revisão — {sprint}*\n{output['slack_summary']}",
                channel="expansao",
            )

        # Human gate: Content -> Publish
        pieces_count = len(output.get("content_pieces", []))
        summary = f"{pieces_count} peça(s) de conteúdo prontas para revisão e publicação."
        approved = self._await_human_approval("content->publish", summary)
        output["human_approved"] = approved

        if approved:
            for piece in output.get("content_pieces", []):
                piece["approved_for_publish"] = True
            console.print("[green]Conteúdo aprovado para publicação![/]")
            self._save_output(output, f"marketing_{safe_sprint}_approved.json")
        else:
            console.print("[yellow]Conteúdo rejeitado. Revise e resubmeta manualmente.[/]")

        return output
