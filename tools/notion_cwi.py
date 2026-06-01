"""Notion CWI integration — databases for the CWI Software pipeline."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import httpx

from orchestrator.settings import settings

_NOTION_VERSION = "2022-06-28"
_BASE_URL = "https://api.notion.com/v1"


class NotionCWIClient:
    """Notion client for CWI-specific databases."""

    def __init__(self) -> None:
        self.client = httpx.Client(
            base_url=_BASE_URL,
            headers={
                "Authorization": f"Bearer {settings.notion_api_key}",
                "Notion-Version": _NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=30,
        )

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self.client.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def _patch(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self.client.patch(path, json=body)
        r.raise_for_status()
        return r.json()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        r = self.client.get(path, params=params or {})
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Meetings
    # ------------------------------------------------------------------

    def get_weekly_meetings(self, days: int = 7) -> list[dict[str, Any]]:
        """Return processed meetings from the last N days (status Ativo)."""
        since = (date.today() - timedelta(days=days)).isoformat()

        r = self._post(f"/databases/{settings.cwi_meetings_db_id}/query", {
            "filter": {
                "and": [
                    {"property": "Status", "select": {"equals": "Ativo"}},
                    {"property": "Date", "date": {"on_or_after": since}},
                ]
            },
            "sorts": [{"property": "Date", "direction": "ascending"}],
            "page_size": 50,
        })

        meetings = []
        for page in r.get("results", []):
            props = page.get("properties", {})
            name = "".join(t.get("plain_text", "") for t in props.get("Name", {}).get("title", []))
            dt   = (props.get("Date", {}).get("date") or {}).get("start", "")
            # Fetch page content (first 30 blocks as plain text)
            content = self._extract_page_text(page["id"])
            meetings.append({"titulo": name, "data": dt, "conteudo": content, "url": page.get("url", "")})

        return meetings

    def _extract_page_text(self, page_id: str) -> str:
        """Extract plain text from a Notion page's top-level blocks."""
        try:
            data = self._get(f"/blocks/{page_id}/children", {"page_size": 50})
        except Exception:
            return ""
        lines = []
        for b in data.get("results", []):
            btype = b.get("type", "")
            rt = b.get(btype, {}).get("rich_text", [])
            text = "".join(t.get("plain_text", "") for t in rt)
            if text.strip():
                lines.append(text)
        return "\n".join(lines)

    def create_meeting_page(self, meeting: dict[str, Any]) -> str:
        """Create a meeting notes page in the CWI Meetings database."""
        acoes = meeting.get("acoes", [])
        acoes_text = "\n".join(
            f"- [{a['id']}] {a['descricao']} — {a['responsavel']} | prazo: {a.get('prazo', 'a definir')} | prioridade: {a.get('prioridade', 'media')}"
            for a in acoes
        )
        decisoes_text = "\n".join(
            f"- {d['decisao']}" for d in meeting.get("decisoes", [])
        )
        pontos_text = "\n".join(f"- {p}" for p in meeting.get("pontos_em_aberto", []))

        page = self._post("/pages", {
            "parent": {"database_id": settings.cwi_meetings_db_id},
            "properties": {
                "Name": {"title": [{"text": {"content": meeting.get("titulo", "Reuniao")}}]},
                "Date": {"date": {"start": meeting.get("data", date.today().isoformat())}},
                "Status": {"select": {"name": "Ativo"}},
            },
            "children": [
                self._heading("Resumo Executivo"),
                self._paragraph(meeting.get("resumo_executivo", "")),
                self._heading("Participantes"),
                self._paragraph(", ".join(meeting.get("participantes", []))),
                self._heading("Decisoes"),
                self._paragraph(decisoes_text or "Nenhuma decisao registrada."),
                self._heading("Acoes"),
                self._paragraph(acoes_text or "Nenhuma acao registrada."),
                self._code_block(json.dumps(acoes, ensure_ascii=False, indent=2), language="json"),
                self._heading("Pontos em Aberto"),
                self._paragraph(pontos_text or "Nenhum ponto em aberto."),
            ],
        })
        return page["id"]

    # ------------------------------------------------------------------
    # Reports (PMO, Agile Coach, Executive)
    # ------------------------------------------------------------------

    def create_report_page(self, report_type: str, data: dict[str, Any]) -> str:
        """Create a report page in the CWI Reports database."""
        page = self._post("/pages", {
            "parent": {"database_id": settings.cwi_reports_db_id},
            "properties": {
                "Name": {"title": [{"text": {"content": f"{report_type} — {data.get('periodo', date.today().isoformat())}"}}]},
                "Date": {"date": {"start": date.today().isoformat()}},
                "Type": {"select": {"name": report_type}},
                "Status": {"select": {"name": data.get("status_geral", "verde").capitalize()}},
            },
            "children": [
                self._heading("Resumo"),
                self._paragraph(data.get("resumo_executivo") or data.get("headline") or ""),
                self._heading("Dados"),
                self._code_block(json.dumps(data, ensure_ascii=False, indent=2), language="json"),
            ],
        })
        return page["id"]

    # ------------------------------------------------------------------
    # Backlog (Product Agent)
    # ------------------------------------------------------------------

    def create_backlog_epic(self, epic: dict[str, Any]) -> str:
        """Create an epic page in the CWI Backlog database."""
        stories_text = "\n\n".join(
            f"**{s['id']}** — {s['titulo']}\n"
            f"{s['historia']}\n"
            f"Criterios:\n" + "\n".join(f"  - {c}" for c in s.get("criterios_de_aceite", [])) +
            f"\nPoints: {s.get('story_points', '?')}  |  MVP: {'Sim' if s.get('is_mvp') else 'Nao'}"
            for s in epic.get("historias", [])
        )
        page = self._post("/pages", {
            "parent": {"database_id": settings.cwi_backlog_db_id},
            "properties": {
                "Name": {"title": [{"text": {"content": f"[{epic['id']}] {epic['titulo']}"}}]},
                "Priority": {"select": {"name": epic.get("prioridade", "media").capitalize()}},
                "Status": {"select": {"name": "Backlog"}},
            },
            "children": [
                self._heading("Valor de Negocio"),
                self._paragraph(epic.get("valor_de_negocio", "")),
                self._heading("Historias"),
                self._paragraph(stories_text or "Sem historias."),
            ],
        })
        return page["id"]

    # ------------------------------------------------------------------
    # Block builders
    # ------------------------------------------------------------------

    @staticmethod
    def _heading(text: str, level: int = 2) -> dict[str, Any]:
        kind = f"heading_{level}"
        return {
            "object": "block",
            "type": kind,
            kind: {"rich_text": [{"type": "text", "text": {"content": text}}]},
        }

    @staticmethod
    def _paragraph(text: str) -> dict[str, Any]:
        return {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]},
        }

    @staticmethod
    def _code_block(code: str, language: str = "plain text") -> dict[str, Any]:
        return {
            "object": "block",
            "type": "code",
            "code": {
                "rich_text": [{"type": "text", "text": {"content": code[:2000]}}],
                "language": language,
            },
        }
