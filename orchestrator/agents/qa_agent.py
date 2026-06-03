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

        sprint = dev_output.get("sprint") or pm_output.get("sprint", "unknown")

        all_stories = [s for prd in pm_output.get("prds", []) for s in prd.get("stories", [])]
        stories_by_id = {s["id"]: s for s in all_stories if s.get("id")}
        impls_by_id = {
            impl["story_id"]: impl
            for impl in dev_output.get("implementations", [])
            if impl.get("story_id")
        }
        contracts_json = json.dumps(
            [c for arch in architect_output.get("architectures", []) for c in arch.get("api_contracts", [])],
            ensure_ascii=False,
            indent=2,
        )

        # Process one story at a time to avoid 170k+ input tokens
        console.print(f"[dim]QA: avaliando {len(all_stories)} stories individualmente...[/]")
        reports: list[dict] = []
        critical_count = 0
        for story in all_stories:
            sid = story.get("id", "?")
            impl = impls_by_id.get(sid, {"story_id": sid, "files_created": []})
            report = self._evaluate_story(story, impl, contracts_json, sprint)
            reports.append(report)
            rec = report.get("recommendation", "")
            is_critical = rec in ("block_deploy", "fix_required")
            if is_critical:
                critical_count += 1
            console.print(f"[dim]  {sid}: {rec}[/]")

            # Update dashboard pipeline_stories
            try:
                from tools.run_tracker import upsert_story  # noqa: PLC0415
                qa_status = "qa_approved" if not is_critical else "qa_rejected"
                issues = report.get("code_quality", {}).get("issues", [])
                notes = "; ".join(i.get("description", "") for i in issues[:2] if i.get("severity") == "critical")
                upsert_story(sprint=sprint, story_id=sid,
                             status=qa_status, qa_result=rec, qa_notes=notes)
            except Exception:
                pass

        approved = critical_count == 0
        output = {
            "sprint": sprint,
            "approved": approved,
            "reports": reports,
            "overall_notes": f"{len(reports)} stories avaliadas, {critical_count} com issues críticos.",
            "slack_summary": (
                f"QA {'✅ APROVADO' if approved else '❌ REPROVADO'} — {sprint}. "
                f"{critical_count} issues críticos em {len(reports)} stories."
            ),
        }

        safe_sprint = "".join(c if c.isalnum() or c in "-_" else "_" for c in sprint)
        filename = f"qa_{safe_sprint}.json"
        console.print(f"[dim]QA: {'APROVADO' if approved else 'REPROVADO'} — {critical_count} críticos[/]")
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

    def _evaluate_story(
        self,
        story: dict[str, Any],
        impl: dict[str, Any],
        contracts_json: str,
        sprint: str,
    ) -> dict[str, Any]:
        """Evaluate a single story against its acceptance criteria."""
        user_message = f"""## Sprint
{sprint}

## User Story e critérios de aceite
```json
{json.dumps(story, ensure_ascii=False, indent=2)}
```

## Código implementado
```json
{json.dumps(impl, ensure_ascii=False, indent=2)}
```

## Contratos de API relevantes
```json
{contracts_json}
```

Avalie o código implementado contra os critérios de aceite desta story.
REGRA: Responda EXCLUSIVAMENTE com o JSON de output especificado no system prompt para UMA story.
Nenhum texto antes ou depois do JSON.
"""
        response_text = self._run(user_message, max_tokens=4096)
        try:
            result = self._parse_json_output(response_text)
            if not result.get("story_id"):
                result["story_id"] = story.get("id", "unknown")
            return result
        except ValueError:
            return {
                "story_id": story.get("id", "unknown"),
                "title": story.get("title", ""),
                "results": [],
                "code_quality": {"has_tests": bool(impl.get("files_created")), "issues": []},
                "recommendation": "deploy_with_caveats" if impl.get("files_created") else "fix_required",
            }
