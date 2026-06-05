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

        # Strip file content from implementations — DevOps only needs metadata
        impl_summary = [
            {
                "story_id": i.get("story_id"),
                "title":    i.get("title", ""),
                "files_created": i.get("files_created", []),
                "notes":    i.get("notes", ""),
            }
            for i in dev_output.get("implementations", [])
        ]
        # Strip report bodies from QA — only pass verdict per story
        qa_summary = {
            "sprint":         qa_output.get("sprint"),
            "approved":       qa_output.get("approved"),
            "human_approved": qa_output.get("human_approved"),
            "overall_notes":  qa_output.get("overall_notes"),
            "stories": [
                {
                    "story_id":       r.get("story_id"),
                    "recommendation": r.get("recommendation"),
                }
                for r in qa_output.get("reports", [])
            ],
        }

        qa_json = json.dumps(qa_summary, ensure_ascii=False, indent=2)
        implementations_json = json.dumps(impl_summary, ensure_ascii=False, indent=2)
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

        # Persist each deploy record to Notion (non-fatal)
        for deploy in output.get("deploys", []):
            try:
                self.notion.create_deploy_page(sprint, deploy)
            except Exception as exc:
                console.print(f"[yellow]Notion deploy persist skipped: {exc}[/]")

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

    def run_deploy_queue(self, by_project: dict[str, list]) -> None:
        """Deploy all queued stories grouped by project. Called by /deploy-queue/execute."""
        from datetime import datetime, timezone  # noqa: PLC0415
        from tools.run_tracker import upsert_story, save_deploy_log  # noqa: PLC0415
        from tools.coolify import CoolifyClient  # noqa: PLC0415
        import httpx  # noqa: PLC0415

        console.rule("[bold]DevOps Agent — Deploy Queue")
        coolify = CoolifyClient()

        for project, stories in by_project.items():
            console.print(f"[blue]Deploying {len(stories)} stories for project '{project}'...[/]")
            uuids = self._project_coolify_uuids(project)
            all_ok = True

            if uuids:
                for app_uuid in uuids:
                    deployment_uuid = ""
                    deploy_status = "error"
                    logs = ""
                    error_msg = ""
                    health_ok = False

                    try:
                        base = settings.coolify_base_url.rstrip("/")
                        headers = {
                            "Authorization": f"Bearer {settings.coolify_api_key}",
                            "Content-Type": "application/json",
                        }
                        r = httpx.post(f"{base}/api/v1/deploy?uuid={app_uuid}&force=false",
                                       headers=headers, timeout=30)
                        r.raise_for_status()
                        data = r.json()
                        deployment_uuid = (data.get("deployments") or [{}])[0].get("deployment_uuid", "")
                        console.print(f"[green]  Triggered: {app_uuid[:12]} → deployment {deployment_uuid[:12] or '?'}[/]")

                        if deployment_uuid:
                            deploy_status = coolify.wait_for_deploy(deployment_uuid, timeout=300, poll_interval=15)
                            logs = coolify.get_deployment_logs(deployment_uuid)
                        else:
                            # Coolify didn't return a deployment_uuid — fallback to app status polling
                            import time  # noqa: PLC0415
                            time.sleep(30)
                            deploy_status = "finished" if coolify.health_check_by_id(app_uuid) else "failed"

                        health_ok = deploy_status == "finished"
                        if not health_ok:
                            all_ok = False
                            console.print(f"[red]  Deploy {deploy_status}: {app_uuid[:12]}[/]")
                        else:
                            console.print(f"[green]  Deploy healthy: {app_uuid[:12]}[/]")

                    except Exception as exc:
                        all_ok = False
                        error_msg = str(exc)
                        console.print(f"[red]  Deploy error for {project}/{app_uuid[:12]}: {exc}[/]")

                    completed = datetime.now(timezone.utc) if deploy_status != "triggered" else None
                    for story in stories:
                        save_deploy_log(
                            story_id=story["story_id"],
                            sprint=story["sprint"],
                            project=project,
                            coolify_app_uuid=app_uuid,
                            coolify_deployment_uuid=deployment_uuid,
                            status=deploy_status,
                            health_ok=health_ok,
                            logs=logs,
                            error_msg=error_msg,
                            completed_at=completed,
                        )
            else:
                console.print(f"[yellow]  No Coolify UUID for '{project}' — marking deployed (manual confirm needed)[/]")
                for story in stories:
                    save_deploy_log(
                        story_id=story["story_id"],
                        sprint=story["sprint"],
                        project=project,
                        status="no_uuid",
                        health_ok=True,
                        completed_at=datetime.now(timezone.utc),
                    )

            new_status = "deployed" if all_ok else "deploy_failed"
            for story in stories:
                try:
                    upsert_story(sprint=story["sprint"], story_id=story["story_id"], status=new_status)
                except Exception:
                    pass
            console.print(f"[{'green' if all_ok else 'red'}]{project}: {len(stories)} stories → {new_status}[/]")

    def _project_coolify_uuids(self, project: str) -> list[str]:
        mapping = {
            "aios":     settings.coolify_uuid_aios,
            "climate":  settings.coolify_uuid_climate,
            "grc-flow": settings.coolify_uuid_grc_flow,
            "cwi":      settings.coolify_uuid_cwi,
        }
        raw = mapping.get(project, "")
        return [u.strip() for u in raw.split(",") if u.strip()]

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
