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

    def run(
        self,
        ceo_output: dict[str, Any] | None = None,
        spec_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate PRDs from CEO plan or approved Spec. Persists to Notion + human gate."""
        console.rule("[bold]PM Agent")

        if spec_data:
            # Input from approved Spec (Watcher flow)
            from datetime import date  # noqa: PLC0415
            sprint = date.today().strftime("%Y-W%W")
            spec_json = json.dumps(spec_data, ensure_ascii=False, indent=2)
            user_message = f"""
## Especificacao Funcional Aprovada
**Sprint**: {sprint}

A especificacao abaixo foi revisada e aprovada. Gere os PRDs e User Stories correspondentes.

```json
{spec_json}
```

Por favor, escreva os PRDs e User Stories para cada caso de uso e epico da spec.
Inclua o JSON de output ao final da sua resposta.
"""
        else:
            # Input from CEO Agent (standard flow)
            ceo_output = ceo_output or {}
            sprint = ceo_output.get("week", "?")
            priorities = ceo_output.get("priorities", [])
            pm_instructions = ceo_output.get("pm_instructions", "")

            # Fetch existing User Stories from Notion PRDs for each priority
            notion_stories = self._fetch_notion_stories(priorities)

            priorities_json = json.dumps(priorities, ensure_ascii=False, indent=2)
            user_message = f"""
## Output do CEO Agent
**Sprint**: {sprint}

### Prioridades aprovadas
```json
{priorities_json}
```

### User Stories já definidas no Notion (USE ESTAS — não invente outras)
```json
{json.dumps(notion_stories, ensure_ascii=False, indent=2)}
```

### Instruções do CEO
{pm_instructions}

IMPORTANTE: Use as User Stories do Notion como base. Enriqueça com critérios de aceite e estimativas, mas não altere IDs nem títulos, e não crie stories que não estejam no Notion.
Inclua o JSON de output ao final da sua resposta.
"""
        response_text = self._run(user_message, max_tokens=32768)

        console.print("\n[dim]--- PM Agent output preview ---[/]")
        console.print(response_text[:800] + ("..." if len(response_text) > 800 else ""))

        output = self._parse_json_output(response_text)

        # Persist output file
        safe_sprint = "".join(c if c.isalnum() or c in "-_" else "_" for c in sprint)
        filename = f"pm_prds_{safe_sprint}.json"
        self._save_output(output, filename)

        # Save each PRD to Notion + register stories in dashboard DB
        from tools.run_tracker import upsert_story  # noqa: PLC0415
        for prd in output.get("prds", []):
            self.notion.create_prd_page(sprint, prd)
            prd_project  = prd.get("project", "expansao")
            epic_id      = prd.get("epic_id", "")
            epic_title   = prd.get("title", "")
            prd_notion_id = prd.get("notion_id", "")
            for story in prd.get("stories", []):
                sid = story.get("id", "")
                if sid:
                    try:
                        upsert_story(
                            sprint=sprint, story_id=sid,
                            title=story.get("title", ""),
                            project=story.get("project", prd_project),
                            epic_id=epic_id, epic_title=epic_title,
                            prd_title=epic_title, status="backlog",
                            notion_id=prd_notion_id,
                        )
                    except Exception:
                        pass

        # Notify Slack
        if settings.slack_webhook_url_expansao and output.get("slack_summary"):
            post_slack_message(f"📝 *PRDs criados — {sprint}*\n{output['slack_summary']}", channel="expansao")

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

    def _fetch_notion_stories(self, priorities: list) -> dict:
        """Fetch existing User Stories from Notion PRD pages for each priority."""
        result = {}
        for p in priorities:
            notion_id = p.get("notion_id", "")
            title = p.get("title", "")
            if not notion_id:
                continue
            try:
                page = self.notion._get(f"/pages/{notion_id}")
                blocks = self.notion._get(f"/blocks/{notion_id}/children").get("results", [])
                stories_text = []
                in_stories = False
                for block in blocks:
                    btype = block.get("type", "")
                    text = ""
                    if btype in ("paragraph", "bulleted_list_item", "numbered_list_item"):
                        rich = block.get(btype, {}).get("rich_text", [])
                        text = "".join(t.get("plain_text", "") for t in rich)
                    elif btype == "heading_2":
                        rich = block.get("heading_2", {}).get("rich_text", [])
                        heading = "".join(t.get("plain_text", "") for t in rich)
                        in_stories = "User Stor" in heading or "Historia" in heading
                    if in_stories and text.strip():
                        stories_text.append(text.strip())
                if stories_text:
                    result[title] = stories_text
            except Exception:
                pass  # If fetch fails, PM generates from scratch
        return result
