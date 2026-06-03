"""Dev Agent — implements code from architecture decisions, one story at a time."""

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

    def run(
        self,
        architect_output: dict[str, Any],
        pm_output: dict[str, Any],
        retry_story_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate implementations per story (one API call each) -> persist -> pass to QA.

        If `retry_story_ids` is given, only those stories are re-implemented and merged
        into the existing dev output for the current sprint.
        """
        console.rule("[bold]Dev Agent")

        sprint = architect_output.get("sprint") or pm_output.get("sprint", "unknown")
        safe_sprint = "".join(c if c.isalnum() or c in "-_" else "_" for c in sprint)
        arch_json = json.dumps(architect_output.get("architectures", []), ensure_ascii=False, indent=2)
        all_stories = [s for prd in pm_output.get("prds", []) for s in prd.get("stories", [])]

        # Partial retry: only re-run stories that failed previously
        if retry_story_ids:
            stories = [s for s in all_stories if s.get("id") in retry_story_ids]
            console.print(f"[yellow]Retry mode: {len(stories)} stories — {retry_story_ids}[/]")
        else:
            stories = all_stories

        # Always load existing implementations — skip stories already done
        existing_impls: dict[str, dict] = self._load_existing_implementations(safe_sprint)
        done_ids = {sid for sid, impl in existing_impls.items() if impl.get("files_created")}
        if done_ids:
            console.print(f"[dim]Aproveitando {len(done_ids)} stories já implementadas: {sorted(done_ids)}[/]")

        console.print(f"[dim]Sprint: {sprint} | Stories: {len(stories)} | Pendentes: {len(stories) - len(done_ids)}[/]")

        sprint_dir = _CODE_OUTPUT_DIR / safe_sprint
        sprint_dir.mkdir(parents=True, exist_ok=True)

        # Start from existing implementations, add/overwrite as we go
        new_implementations: list[dict[str, Any]] = list(existing_impls.values())

        for i, story in enumerate(stories, 1):
            story_id = story.get("id", f"US-{i:03d}")

            # Skip stories that are already implemented with files
            if story_id in done_ids and not (retry_story_ids and story_id in retry_story_ids):
                console.print(f"[dim]  Pulando {story_id} (já implementada)[/]")
                continue

            console.print(f"[dim]  Implementando {story_id} ({i}/{len(stories)})...[/]")
            impl = self._implement_story(story, arch_json, sprint)

            # Guarantee story_id is set from input (model may omit it)
            if not impl.get("story_id"):
                impl["story_id"] = story_id

            # Retry once if no files were generated
            if not impl.get("files_created"):
                console.print(f"[yellow]  {story_id}: sem arquivos — retentando...[/]")
                impl = self._implement_story(story, arch_json, sprint)
                if not impl.get("story_id"):
                    impl["story_id"] = story_id

            # Update or append in new_implementations list
            existing_idx = next((j for j, x in enumerate(new_implementations) if x.get("story_id") == story_id), None)
            if existing_idx is not None:
                new_implementations[existing_idx] = impl
            else:
                new_implementations.append(impl)

            self._write_code_files(sprint_dir, impl)

            # Update dashboard pipeline_stories
            n_files = len(impl.get("files_created", []))
            if n_files > 0:
                try:
                    from tools.run_tracker import upsert_story  # noqa: PLC0415
                    upsert_story(sprint=sprint, story_id=story_id,
                                 title=story.get("title", ""), status="dev", dev_files=n_files)
                except Exception:
                    pass

            # Save incrementally after each story so a crash doesn't lose progress
            self._save_output(
                {"sprint": sprint, "implementations": new_implementations,
                 "slack_summary": f"{len(new_implementations)} stories implementadas até agora"},
                f"dev_{safe_sprint}.json",
            )

        implementations = new_implementations

        output: dict[str, Any] = {
            "sprint": sprint,
            "implementations": implementations,
            "slack_summary": f"{len(implementations)} stories implementadas — sprint {sprint}",
        }

        filename = f"dev_{safe_sprint}.json"
        self._save_output(output, filename)

        # Update Notion stories to "In Review"
        for prd in pm_output.get("prds", []):
            if prd.get("notion_id"):
                self.notion.update_page_status(prd["notion_id"], "In Review")

        if settings.slack_webhook_url_expansao:
            post_slack_message(
                f"💻 *Código implementado — {sprint}*\n{output['slack_summary']}",
                channel="expansao",
            )

        console.print("[green]Implementação concluída. Acionando QA Agent...[/]")
        return output

    def _implement_story(
        self, story: dict[str, Any], arch_json: str, sprint: str
    ) -> dict[str, Any]:
        """Generate implementation JSON for a single User Story."""
        story_json = json.dumps(story, ensure_ascii=False, indent=2)
        user_message = f"""## Sprint
{sprint}

## Arquitetura definida (Architect Agent)
```json
{arch_json}
```

## User Story a implementar
```json
{story_json}
```

REGRA CRÍTICA: Responda EXCLUSIVAMENTE com o JSON de output especificado no system prompt para UMA story.
Nenhum texto antes ou depois do JSON. Código completo no campo "content" de cada arquivo.
"""
        response_text = self._run(user_message, max_tokens=16384)
        try:
            result = self._parse_json_output(response_text)
            # Ensure story_id comes from input if model omitted it
            if not result.get("story_id"):
                result["story_id"] = story.get("id", "unknown")
            return result
        except ValueError:
            story_id = story.get("id", "unknown")
            console.print(f"[yellow]  Parse error em {story_id} — stub vazio[/]")
            return {
                "story_id": story_id,
                "title": story.get("title", ""),
                "files_created": [],
                "migration_command": "",
                "tests_created": [],
                "done_criteria_met": [],
                "notes": "Parse error — resposta do modelo não retornou JSON válido.",
            }

    def _load_existing_implementations(self, safe_sprint: str) -> dict[str, dict]:
        """Load previously saved implementations keyed by story_id."""
        out_dir = Path(settings.output_dir)
        files = sorted(
            out_dir.glob(f"dev_{safe_sprint}*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return {}
        try:
            data = json.loads(files[0].read_text(encoding="utf-8"))
            return {impl["story_id"]: impl for impl in data.get("implementations", []) if impl.get("story_id")}
        except Exception:
            return {}

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
        import shutil  # noqa: PLC0415
        return shutil.which("claude")

    @staticmethod
    def _write_code_files(base_dir: Path, impl: dict[str, Any]) -> None:
        story_id = (impl.get("story_id") or "unknown").replace("/", "_")
        story_dir = base_dir / story_id
        story_dir.mkdir(parents=True, exist_ok=True)

        for f in impl.get("files_created", []):
            file_path = story_dir / Path(f["path"]).name
            file_path.write_text(f.get("content", ""), encoding="utf-8")
            console.print(f"[dim]  -> wrote {file_path}[/]")

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
