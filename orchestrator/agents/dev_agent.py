"""Dev Agent — implements code from architecture decisions, optionally via Claude Code."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from rich.console import Console

from orchestrator.base_agent import BaseAgent
from orchestrator.settings import settings
from tools.notion import NotionClient
from tools.slack import post_slack_message

console = Console(legacy_windows=False)

_CODE_OUTPUT_DIR = Path(settings.output_dir) / "code"


class DevAgent(BaseAgent):
    name = "Dev Agent"
    role = "Software Developer"
    prompt_file = "agents/prompts/dev.md"

    def __init__(self) -> None:
        self.model = settings.dev_model
        super().__init__()
        self.notion = NotionClient()

    def run(self, architect_output: dict[str, Any], pm_output: dict[str, Any]) -> dict[str, Any]:
        """Generate implementations from architecture + PRDs -> persist -> pass to QA."""
        console.rule("[bold]Dev Agent")

        sprint = architect_output.get("sprint", "?")
        arch_json = json.dumps(architect_output.get("architectures", []), ensure_ascii=False, indent=2)
        stories_json = json.dumps(
            [s for prd in pm_output.get("prds", []) for s in prd.get("stories", [])],
            ensure_ascii=False,
            indent=2,
        )

        user_message = f"""
## Sprint
{sprint}

## Arquiteturas definidas (Architect Agent)
```json
{arch_json}
```

## User Stories a implementar (PM Agent)
```json
{stories_json}
```

Implemente o código para cada User Story seguindo a arquitetura e as instruções do Architect Agent.
Para cada story, gere o código completo dos arquivos necessários.
Inclua o JSON de output ao final da sua resposta.
"""
        response_text = self._run(user_message, max_tokens=16384)
        console.print("\n[dim]--- Dev Agent output preview ---[/]")
        console.print(response_text[:800] + ("..." if len(response_text) > 800 else ""))

        output = self._parse_json_output(response_text)

        # Persist code files to outputs/code/{sprint}/
        sprint_dir = _CODE_OUTPUT_DIR / sprint.replace("-", "_")
        sprint_dir.mkdir(parents=True, exist_ok=True)
        for impl in output.get("implementations", []):
            self._write_code_files(sprint_dir, impl)

        filename = f"dev_{sprint.replace('-', '_')}.json"
        self._save_output(output, filename)

        # Update Notion stories to "In Review"
        for prd in pm_output.get("prds", []):
            if prd.get("notion_id"):
                self.notion.update_page_status(prd["notion_id"], "In Review")

        if settings.slack_webhook_url and output.get("slack_summary"):
            post_slack_message(f"💻 *Código implementado — {sprint}*\n{output['slack_summary']}")

        console.print("[green]Implementação concluída. Acionando QA Agent...[/]")
        return output

    def run_with_claude_code(
        self, prompt: str, project_dir: str, timeout: int = 300
    ) -> str:
        """Invoke Claude Code CLI for assisted code generation inside a target project."""
        claude_bin = self._find_claude_binary()
        if not claude_bin:
            console.print("[yellow]Claude Code CLI not found — falling back to direct Claude API.[/]")
            return self._run(prompt)

        console.print(f"[blue]-> Invoking Claude Code in {project_dir}[/]")
        result = subprocess.run(
            [claude_bin, "--print", prompt],
            capture_output=True,
            text=True,
            cwd=project_dir,
            timeout=timeout,
        )
        if result.returncode != 0:
            console.print(f"[yellow]Claude Code stderr: {result.stderr[:300]}[/]")
        return result.stdout

    @staticmethod
    def _find_claude_binary() -> str | None:
        """Locate the `claude` CLI binary."""
        import shutil  # noqa: PLC0415
        return shutil.which("claude")

    @staticmethod
    def _write_code_files(base_dir: Path, impl: dict[str, Any]) -> None:
        story_id = impl.get("story_id", "unknown").replace("/", "_")
        story_dir = base_dir / story_id
        story_dir.mkdir(parents=True, exist_ok=True)

        for f in impl.get("files_created", []):
            file_path = story_dir / Path(f["path"]).name
            file_path.write_text(f.get("content", ""), encoding="utf-8")
            console.print(f"[dim]  -> wrote {file_path}[/]")

        # Write a summary instructions file
        notes_path = story_dir / "APPLY.md"
        lines = [
            f"# {impl.get('title', story_id)}\n",
            "## Files to apply\n",
            *[f"- `{f['path']}` ({f.get('action', 'create')})" for f in impl.get("files_created", [])],
            "",
            f"## Migration\n```\n{impl.get('migration_command', 'N/A')}\n```",
            "",
            f"## Notes\n{impl.get('notes', '')}",
        ]
        notes_path.write_text("\n".join(lines), encoding="utf-8")
