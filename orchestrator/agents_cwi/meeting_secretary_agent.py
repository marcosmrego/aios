"""CWI Meeting Secretary Agent — transforms raw transcripts into structured meeting docs."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console

from orchestrator.base_agent import BaseAgent
from orchestrator.settings import settings
from tools.notion_cwi import NotionCWIClient
from tools.slack import post_slack_message

console = Console(legacy_windows=False)

_NOTION_VERSION = "2022-06-28"
_BASE_URL = "https://api.notion.com/v1"


class MeetingSecretaryAgent(BaseAgent):
    name = "Meeting Secretary Agent"
    role = "Secretario de Reuniao"
    model: str = settings.secretary_model
    prompt_file = "agents/prompts_cwi/meeting_secretary.md"

    def __init__(self) -> None:
        self.model = settings.secretary_model
        super().__init__()
        self.notion = NotionCWIClient()
        self._http = httpx.Client(
            base_url=_BASE_URL,
            headers={
                "Authorization": f"Bearer {settings.notion_api_key}",
                "Notion-Version": _NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=30,
        )

    def run(
        self,
        transcript: str = "",
        transcript_file: str = "",
        notion_page_id: str = "",
        entry_page_id: str = "",
    ) -> dict[str, Any]:
        """Process a meeting transcript and produce structured output.

        Input priority: notion_page_id > transcript_file > transcript > auto-detect file.
        When notion_page_id is provided, reads content from Notion directly.
        entry_page_id is the control database row to mark as 'Gerado' after processing.
        """
        console.rule("[bold]Meeting Secretary Agent")

        # 1. Load transcript
        if notion_page_id:
            transcript = self._read_notion_page(notion_page_id)
            console.print(f"[dim]Transcricao lida do Notion ({len(transcript)} chars)[/]")
        elif transcript_file:
            transcript = Path(transcript_file).read_text(encoding="utf-8")
            console.print(f"[dim]Transcricao carregada do arquivo ({len(transcript)} chars)[/]")
        elif not transcript:
            transcript = self._load_latest_input("transcricao")
            console.print(f"[dim]Transcricao carregada (auto-detect, {len(transcript)} chars)[/]")

        if not transcript.strip():
            console.print("[yellow]Transcricao vazia — entrada ignorada.[/]")
            raise ValueError("Transcricao vazia")

        # 2. Detect intent — route non-meeting inputs to the right agent
        intent = self._classify_intent(transcript)
        console.print(f"[dim]Intencao detectada: {intent}[/]")

        if intent == "spec":
            from orchestrator.agents.spec_agent import SpecAgent  # noqa: PLC0415
            result = SpecAgent().run(input_text=transcript, pipeline="CWI", origem="Reunião")
            return {"intent": "spec", "spec_notion_page": result.get("notion_page_id", ""), **result.get("spec", {})}

        if intent == "diagram":
            from orchestrator.agents.diagram_agent import DiagramAgent  # noqa: PLC0415
            paths = DiagramAgent().run(input_text=transcript)
            return {"intent": "diagram", "files": [str(p) for p in paths]}

        if intent == "command":
            console.print("[yellow]Comando generico recebido — nao processado pelo Secretary.[/]")
            return {"intent": "command", "input": transcript[:200]}

        # 3. Process as meeting transcript (default)
        user_message = f"""Processe a transcricao abaixo e gere o output estruturado conforme instrucoes.

TRANSCRICAO:
{transcript}

Retorne apenas o JSON, sem texto adicional."""

        response_text = self._run(user_message, max_tokens=8192)

        # 4. Parse output
        output = self._parse_json_output(response_text)
        output["intent"] = "meeting"

        # 5. Save locally
        today = date.today().strftime("%Y_%m_%d")
        self._save_output(output, f"cwi/meeting_{today}.json")

        # 6. Save to Notion CWI Meetings
        if settings.cwi_meetings_db_id:
            self.notion.create_meeting_page(output)

        # 7. Mark control entry as Gerado
        if entry_page_id:
            self._mark_entry_gerado(entry_page_id, output)

        # 8. Notify Slack
        if settings.slack_webhook_url_cwi and output.get("slack_summary"):
            post_slack_message(f"[REUNIAO] *{output.get('titulo', 'Reuniao')}*\n{output['slack_summary']}", channel="cwi")

        # 9. Trigger task digest so pending tasks are always up to date
        try:
            from orchestrator.agents_cwi.task_digest_agent import TaskDigestAgent  # noqa: PLC0415
            TaskDigestAgent().run(triggered_by="meeting")
        except Exception as e:
            console.print(f"[yellow]Aviso: digest de tarefas nao gerado: {e}[/]")

        console.print("[green]Meeting Secretary concluido.[/]")
        return output

    def _classify_intent(self, text: str) -> str:
        """Classify input as 'meeting', 'spec', 'diagram', or 'command' using Claude Haiku."""
        prompt = """Classifique o texto abaixo em uma das categorias:
- "meeting": transcricao de reuniao (pessoas falando, discussoes, decisoes)
- "spec": pedido para gerar especificacao funcional de um sistema ou feature
- "diagram": pedido para gerar diagrama de fluxo ou mapa de processo
- "command": outro comando ou pergunta

Responda APENAS com uma palavra: meeting, spec, diagram ou command."""

        try:
            resp = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=10,
                system=prompt,
                messages=[{"role": "user", "content": text[:2000]}],
            )
            intent = resp.content[0].text.strip().lower()  # type: ignore[union-attr]
            return intent if intent in ("meeting", "spec", "diagram", "command") else "meeting"
        except Exception:
            return "meeting"  # fail-safe: treat as meeting

    def _read_notion_page(self, page_id: str) -> str:
        """Fetch all text blocks from a Notion page and return as plain text."""
        # Normalize page_id format
        pid = page_id.replace("-", "")
        if len(pid) == 32:
            page_id = f"{pid[:8]}-{pid[8:12]}-{pid[12:16]}-{pid[16:20]}-{pid[20:]}"

        all_text: list[str] = []
        cursor = None

        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor

            r = self._http.get(f"/blocks/{page_id}/children", params=params)
            r.raise_for_status()
            data = r.json()

            for block in data.get("results", []):
                btype = block.get("type", "")
                content = block.get(btype, {})
                texts = content.get("rich_text", [])
                text = "".join(t.get("plain_text", "") for t in texts)
                if text.strip():
                    all_text.append(text)

            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        return "\n".join(all_text)

    def _mark_entry_gerado(self, entry_page_id: str, output: dict[str, Any]) -> None:
        """Mark the control database entry as Gerado after processing."""
        try:
            self._http.patch(f"/pages/{entry_page_id}", json={
                "properties": {
                    "Status": {"select": {"name": "Gerado"}},
                    "Titulo": {"title": [{"text": {"content": output.get("titulo", "Reuniao processada")}}]},
                }
            }).raise_for_status()
        except Exception as e:
            console.print(f"[yellow]Aviso: nao foi possivel atualizar status no Notion: {e}[/]")

    @staticmethod
    def _load_latest_input(prefix: str) -> str:
        input_dir = Path("inputs/cwi/")
        files = sorted(input_dir.glob(f"{prefix}*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0].read_text(encoding="utf-8") if files else ""
