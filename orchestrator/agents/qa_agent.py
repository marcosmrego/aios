"""QA Agent — validates implementations against acceptance criteria."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console

from orchestrator.base_agent import BaseAgent
from orchestrator.settings import settings
from tools.notion import NotionClient
from tools.slack import post_slack_message

console = Console(legacy_windows=False)


class QAAgent(BaseAgent):
    name = "QA Agent"
    role = "Quality Assurance Engineer"
    prompt_file = "agents/prompts/qa.md"

    def __init__(self) -> None:
        self.model = settings.qa_model
        super().__init__()
        self.notion = NotionClient()

    def run(
        self,
        dev_output: dict[str, Any],
        pm_output: dict[str, Any],
        architect_output: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate implementations and emit deploy gate decision."""
        console.rule("[bold]QA Agent")

        sprint = dev_output.get("sprint", "?")
        implementations_json = json.dumps(dev_output.get("implementations", []), ensure_ascii=False, indent=2)
        stories_json = json.dumps(
            [s for prd in pm_output.get("prds", []) for s in prd.get("stories", [])],
            ensure_ascii=False,
            indent=2,
        )
        contracts_json = json.dumps(
            [c for arch in architect_output.get("architectures", []) for c in arch.get("api_contracts", [])],
            ensure_ascii=False,
            indent=2,
        )

        user_message = f"""
## Sprint
{sprint}

## Código implementado (Dev Agent)
```json
{implementations_json}
```

## User Stories e critérios de aceite (PM Agent)
```json
{stories_json}
```

## Contratos de API (Architect Agent)
```json
{contracts_json}
```

Analise o código implementado e valide cada critério de aceite de cada User Story.
Verifique qualidade de código, cobertura de testes e contratos de API.
Emita APROVADO ou REPROVADO com justificativa clara.
Inclua o JSON de output ao final da sua resposta.
"""
        response_text = self._run(user_message, max_tokens=4096)
        console.print("\n[dim]--- QA Agent output preview ---[/]")
        console.print(response_text[:800] + ("..." if len(response_text) > 800 else ""))

        output = self._parse_json_output(response_text)
        approved = output.get("approved", False)

        filename = f"qa_{sprint.replace('-', '_')}.json"
        self._save_output(output, filename)

        # Persist QA report to Notion
        for report in output.get("reports", []):
            self.notion.create_qa_report_page(sprint, report)

        # Slack notification
        status_icon = "[OK]" if approved else "[FAIL]"
        if settings.slack_webhook_url_expansao and output.get("slack_summary"):
            post_slack_message(
                f"{status_icon} *QA Report — {sprint}*\n{output['slack_summary']}",
                channel="expansao",
            )

        # Human-in-the-loop gate QA -> Deploy
        critical_issues = [
            issue
            for report in output.get("reports", [])
            for issue in report.get("code_quality", {}).get("issues", [])
            if issue.get("severity") == "critical"
        ]
        if critical_issues:
            console.print(f"[red bold]⚠ {len(critical_issues)} critical issue(s) found — deploy blocked.[/]")
            for issue in critical_issues:
                console.print(f"  [red]• {issue['description']}[/]")

        summary = (
            f"QA {'APROVADO' if approved else 'REPROVADO'} para {sprint}. "
            + (f"{len(critical_issues)} critical issues." if critical_issues else "Sem issues críticos.")
        )
        gate_approved = self._await_human_approval("qa->deploy", summary)
        output["human_approved"] = gate_approved

        if not gate_approved:
            console.print("[red]Deploy rejeitado pelo humano. Retornando ao Dev Agent...[/]")
        else:
            console.print("[green]Deploy aprovado. Acionando DevOps Agent...[/]")

        return output
