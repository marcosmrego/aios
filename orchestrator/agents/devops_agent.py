"""DevOps Agent — deploys via N8N + Coolify after QA approval."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from rich.console import Console

from orchestrator.base_agent import BaseAgent
from orchestrator.settings import settings
from tools.notion import NotionClient
from tools.slack import post_slack_message

console = Console(legacy_windows=False)


class DevOpsAgent(BaseAgent):
    name = "DevOps Agent"
    role = "DevOps Engineer"
    prompt_file = "agents/prompts/devops.md"

    def __init__(self) -> None:
        self.model = settings.devops_model
        super().__init__()
        self.notion = NotionClient()

    def run(
        self,
        qa_output: dict[str, Any],
        dev_output: dict[str, Any],
        architect_output: dict[str, Any],
    ) -> dict[str, Any]:
        """Plan and execute deploys using N8N + Coolify."""
        console.rule("[bold]DevOps Agent")

        # human_approved=True (gate passou) tem precedência sobre approved (avaliação do modelo)
        gate_passed = qa_output.get("human_approved", qa_output.get("approved", False))
        if not gate_passed:
            console.print("[red]QA não aprovado. Deploy cancelado.[/]")
            return {"sprint": qa_output.get("sprint"), "deploys": [], "aborted": True}

        sprint = qa_output.get("sprint") or dev_output.get("sprint") or architect_output.get("sprint", "unknown")
        qa_json = json.dumps(qa_output, ensure_ascii=False, indent=2)
        implementations_json = json.dumps(
            dev_output.get("implementations", []), ensure_ascii=False, indent=2
        )
        stack_json = json.dumps(
            [a.get("stack_decisions", {}) for a in architect_output.get("architectures", [])],
            ensure_ascii=False,
            indent=2,
        )

        user_message = f"""
## Sprint
{sprint}

## QA Report (aprovado)
```json
{qa_json}
```

## Implementações (Dev Agent)
```json
{implementations_json}
```

## Stack e dependências (Architect Agent)
```json
{stack_json}
```

Planeje e descreva o processo de deploy para cada implementação aprovada.
Liste os projetos a fazer deploy, migrations a aplicar, health checks e riscos.
Inclua o JSON de output ao final da sua resposta.
"""
        response_text = self._run(user_message, max_tokens=16384)
        console.print("\n[dim]--- DevOps Agent output preview ---[/]")
        console.print(response_text[:600] + ("..." if len(response_text) > 600 else ""))

        output = self._parse_json_output(response_text)

        # Execute deploys via N8N (if configured)
        if settings.n8n_base_url:
            output = self._execute_deploys(output)
        else:
            console.print("[yellow]N8N not configured — deploy plan generated but not executed.[/]")
            for deploy in output.get("deploys", []):
                deploy["status"] = "planned"

        # Add timestamps
        for deploy in output.get("deploys", []):
            deploy.setdefault("deploy_timestamp", datetime.now(timezone.utc).isoformat())

        safe_sprint = "".join(c if c.isalnum() or c in "-_" else "_" for c in sprint)
        filename = f"devops_{safe_sprint}.json"
        self._save_output(output, filename)

        # Persist each deploy record to Notion
        for deploy in output.get("deploys", []):
            self.notion.create_deploy_page(sprint, deploy)

        # Slack notification
        success_count = sum(1 for d in output.get("deploys", []) if d.get("status") == "success")
        total = len(output.get("deploys", []))
        status_icon = "[DEPLOY]" if success_count == total else "[WARN]"
        if settings.slack_webhook_url_expansao and output.get("slack_summary"):
            post_slack_message(
                f"{status_icon} *Deploy — {sprint}* ({success_count}/{total} ok)\n{output['slack_summary']}",
                channel="expansao",
            )

        all_success = all(d.get("status") in ("success", "planned") for d in output.get("deploys", []))
        output["deploy_success"] = all_success
        console.print(
            f"[green]Deploy concluído ({success_count}/{total}).[/]"
            if all_success
            else f"[red]Deploy com falhas ({total - success_count}/{total} falharam).[/]"
        )
        return output

    def _execute_deploys(self, output: dict[str, Any]) -> dict[str, Any]:
        """Trigger N8N deploy webhook for each planned deploy."""
        from tools.n8n import N8NClient  # noqa: PLC0415
        from tools.coolify import CoolifyClient  # noqa: PLC0415

        try:
            n8n = N8NClient()
            coolify = CoolifyClient()
        except RuntimeError as e:
            console.print(f"[yellow]Integration not available: {e}[/]")
            return output

        for deploy in output.get("deploys", []):
            try:
                console.print(f"[blue]-> Deploying {deploy.get('service', '?')}...[/]")
                n8n.trigger_webhook(
                    settings.n8n_deploy_webhook,
                    {
                        "project":            deploy.get("project"),
                        "service":            deploy.get("service"),
                        "environment":        deploy.get("environment", "production"),
                        "migration_command":  deploy.get("migration_command", ""),
                        "callback_url":       f"{settings.aios_api_url}/devops/deploy-callback",
                        "sprint":             output.get("sprint", ""),
                    },
                )
                # Health check via Coolify
                app_id = deploy.get("coolify_app_id", "")
                if app_id:
                    healthy = coolify.health_check_by_id(app_id)
                    deploy["health_check_passed"] = healthy
                    deploy["status"] = "success" if healthy else "failed"
                    if not healthy:
                        console.print(f"[red]Health check failed: {app_id}[/]")
                else:
                    deploy["status"] = "triggered"
                    deploy["health_check_passed"] = None
                    console.print("[dim]No coolify_app_id — deploy triggered, callback will confirm.[/]")
            except Exception as exc:
                console.print(f"[red]Deploy error: {exc}[/]")
                deploy["status"] = "failed"
                deploy["logs"] = str(exc)

        return output
