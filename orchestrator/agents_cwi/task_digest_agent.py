"""CWI Task Digest Agent — scans all meeting actions and produces a pending tasks report."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx
from rich.console import Console

from orchestrator.settings import settings
from tools.slack import post_slack_message

console = Console(legacy_windows=False)

_NOTION_VERSION = "2022-06-28"
_BASE_URL = "https://api.notion.com/v1"


class TaskDigestAgent:
    """Scans CWI Meetings for open actions and publishes a digest to CWI Reports."""

    def __init__(self) -> None:
        self.http = httpx.Client(
            base_url=_BASE_URL,
            headers={
                "Authorization": f"Bearer {settings.notion_api_key}",
                "Notion-Version": _NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=30,
        )

    def run(self, triggered_by: str = "daily") -> dict[str, Any]:
        """
        Scan all Ativo meetings in CWI Meetings, extract open actions and publish digest.
        triggered_by: 'daily' (scheduled 08h) | 'meeting' (post-processing)
        """
        console.rule("[bold]Task Digest Agent")

        # 1. Fetch all meetings with status Ativo
        meetings = self._fetch_all_meetings()
        console.print(f"[dim]{len(meetings)} reuniao(oes) encontrada(s)[/]")

        # 2. Extract all actions from each meeting
        all_actions = self._extract_actions(meetings)
        console.print(f"[dim]{len(all_actions)} acoes encontradas[/]")

        if not all_actions:
            console.print("[yellow]Nenhuma acao encontrada nas atas.[/]")
            return {"total": 0}

        # 3. Separate by priority
        alta   = [a for a in all_actions if a["prioridade"] == "alta"]
        media  = [a for a in all_actions if a["prioridade"] == "media"]
        baixa  = [a for a in all_actions if a["prioridade"] == "baixa"]
        s_def  = [a for a in all_actions if a["prioridade"] not in ("alta", "media", "baixa")]

        output = {
            "data": date.today().isoformat(),
            "triggered_by": triggered_by,
            "total_acoes": len(all_actions),
            "alta": len(alta),
            "media": len(media),
            "baixa": len(baixa),
            "acoes": all_actions,
        }

        # 4. Save digest to Notion CWI Reports
        page_url = self._create_digest_page(output, alta, media, baixa + s_def)
        console.print(f"[green]Digest salvo no Notion[/]")

        # 5. Notify Slack (if configured)
        if settings.slack_webhook_url:
            self._notify_slack(output, alta)

        return output

    # ------------------------------------------------------------------

    def _fetch_all_meetings(self) -> list[dict[str, Any]]:
        results = []
        cursor = None
        while True:
            body: dict[str, Any] = {
                "filter": {"property": "Status", "select": {"equals": "Ativo"}},
                "sorts": [{"property": "Date", "direction": "descending"}],
                "page_size": 50,
            }
            if cursor:
                body["start_cursor"] = cursor
            r = self.http.post(f"/databases/{settings.cwi_meetings_db_id}/query", json=body)
            r.raise_for_status()
            data = r.json()
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return results

    def _extract_actions(self, meetings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fetch blocks from each meeting page and extract actions from JSON code block."""
        import json as _json  # noqa: PLC0415
        all_actions: list[dict[str, Any]] = []

        for meeting in meetings:
            props  = meeting.get("properties", {})
            titulo = "".join(t.get("plain_text", "") for t in props.get("Name", {}).get("title", []))
            data   = (props.get("Date", {}).get("date") or {}).get("start", "")
            url    = meeting.get("url", "")

            r = self.http.get(f"/blocks/{meeting['id']}/children", params={"page_size": 100})
            if r.status_code != 200:
                continue

            for block in r.json().get("results", []):
                if block.get("type") != "code":
                    continue
                rt   = block.get("code", {}).get("rich_text", [])
                text = "".join(t.get("plain_text", "") for t in rt).strip()
                if not text.startswith("["):
                    continue
                try:
                    acoes = _json.loads(text)
                    for a in acoes:
                        all_actions.append({
                            "id":           a.get("id", "?"),
                            "descricao":    a.get("descricao", ""),
                            "responsavel":  a.get("responsavel", "a definir"),
                            "prazo":        a.get("prazo", "a definir"),
                            "prioridade":   a.get("prioridade", "media"),
                            "reuniao":      titulo,
                            "data_reuniao": data,
                            "url":          url,
                        })
                except Exception:
                    continue

        return all_actions

    def _create_digest_page(
        self,
        output: dict[str, Any],
        alta: list,
        media: list,
        baixa: list,
    ) -> str:
        today = date.today().isoformat()
        children: list[dict[str, Any]] = []

        def _heading(text: str, level: int = 2) -> dict:
            kind = f"heading_{level}"
            return {"object": "block", "type": kind, kind: {
                "rich_text": [{"type": "text", "text": {"content": text}}]
            }}

        def _para(text: str) -> dict:
            return {"object": "block", "type": "paragraph", "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
            }}

        def _bullet(text: str) -> dict:
            return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
            }}

        children.append(_para(
            f"Total de acoes abertas: {output['total_acoes']}  |  "
            f"Alta: {output['alta']}  |  Media: {output['media']}  |  Baixa: {output['baixa']}"
        ))

        if alta:
            children.append(_heading("Alta Prioridade", 2))
            for a in alta:
                children.append(_bullet(
                    f"[{a['id']}] {a['descricao']} — {a['responsavel']} | prazo: {a['prazo']} | {a['reuniao']}"
                ))

        if media:
            children.append(_heading("Media Prioridade", 2))
            for a in media:
                children.append(_bullet(
                    f"[{a['id']}] {a['descricao']} — {a['responsavel']} | prazo: {a['prazo']} | {a['reuniao']}"
                ))

        if baixa:
            children.append(_heading("Baixa Prioridade / Sem Classificacao", 2))
            for a in baixa:
                children.append(_bullet(
                    f"[{a['id']}] {a['descricao']} — {a['responsavel']} | prazo: {a['prazo']} | {a['reuniao']}"
                ))

        r = self.http.post("/pages", json={
            "parent": {"database_id": settings.cwi_reports_db_id},
            "properties": {
                "Name":   {"title": [{"text": {"content": f"Pendencias — {today}"}}]},
                "Date":   {"date": {"start": today}},
                "Type":   {"select": {"name": "Pendencias"}},
                "Status": {"select": {"name": "Verde" if output["alta"] == 0 else "Amarelo" if output["alta"] < 4 else "Vermelho"}},
            },
            "children": children[:100],
        })
        r.raise_for_status()
        return r.json().get("url", "")

    @staticmethod
    def _notify_slack(output: dict[str, Any], alta: list) -> None:
        total = output["total_acoes"]
        n_alta = output["alta"]
        lines = [f"[PENDENCIAS] *Digest de Tarefas — {output['data']}*"]
        lines.append(f"Total aberto: {total}  |  Alta prioridade: {n_alta}")
        if alta:
            lines.append("\n*Alta prioridade:*")
            for a in alta[:5]:
                lines.append(f"  - [{a['id']}] {a['descricao'][:60]} — {a['responsavel']}")
            if len(alta) > 5:
                lines.append(f"  ... +{len(alta) - 5} mais")
        post_slack_message("\n".join(lines), channel="cwi-aios")
