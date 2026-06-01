"""Notion API integration — shared memory layer for all agents."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx

from orchestrator.settings import settings

_NOTION_VERSION = "2022-06-28"
_BASE_URL = "https://api.notion.com/v1"


class NotionClient:
    """Thin wrapper around the Notion REST API for AIOS operations."""

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

    def _get(self, path: str) -> dict[str, Any]:
        r = self.client.get(path)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self.client.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def _patch(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self.client.patch(path, json=body)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Backlog
    # ------------------------------------------------------------------

    def get_backlog(self, status_filter: list[str] | None = None) -> list[dict[str, Any]]:
        """Return all backlog items, optionally filtered by status."""
        filters: list[dict[str, Any]] = []
        if status_filter:
            filters = [
                {
                    "property": "Status",
                    "select": {"equals": s},
                }
                for s in status_filter
            ]

        body: dict[str, Any] = {
            "sorts": [{"property": "Priority", "direction": "descending"}],
        }
        if filters:
            body["filter"] = {"or": filters} if len(filters) > 1 else filters[0]

        results = self._post(
            f"/databases/{settings.notion_backlog_db_id}/query", body
        ).get("results", [])
        return [self._parse_backlog_item(r) for r in results]

    def _parse_backlog_item(self, page: dict[str, Any]) -> dict[str, Any]:
        props = page.get("properties", {})
        return {
            "notion_id": page["id"],
            "title": self._text(props.get("Name") or props.get("Title")),
            "description": self._text(props.get("Description")),
            "status": self._select(props.get("Status")),
            "priority": self._select(props.get("Priority")),
            "project": self._select(props.get("Project")),
            "effort_points": self._number(props.get("Effort")),
            "tags": self._multi_select(props.get("Tags")),
            "url": page.get("url", ""),
        }

    # ------------------------------------------------------------------
    # Sprints
    # ------------------------------------------------------------------

    def create_sprint_page(self, week: str, ceo_output: dict[str, Any]) -> str:
        """Create a sprint page in the Sprints database. Returns page ID."""
        priorities_md = "\n".join(
            f"- **{p['title']}** — {p.get('business_justification', '')}"
            for p in ceo_output.get("priorities", [])
        )
        page = self._post("/pages", {
            "parent": {"database_id": settings.notion_sprints_db_id},
            "properties": {
                "Name": {"title": [{"text": {"content": f"Sprint {week}"}}]},
                "Week": {"rich_text": [{"text": {"content": week}}]},
                "Status": {"select": {"name": "Planning"}},
                "Date": {"date": {"start": date.today().isoformat()}},
            },
            "children": [
                self._heading("Prioridades da Semana"),
                self._paragraph(priorities_md),
                self._heading("Métricas de Sucesso"),
                self._paragraph("\n".join(f"- {m}" for m in ceo_output.get("success_metrics", []))),
                self._heading("Riscos"),
                self._paragraph("\n".join(f"- {r}" for r in ceo_output.get("risks", []))),
                self._heading("Instruções para PM Agent"),
                self._paragraph(ceo_output.get("pm_instructions", "")),
            ],
        })
        return page["id"]

    def create_prd_page(self, sprint_week: str, prd: dict[str, Any]) -> str:
        """Create a PRD page in the Projects database. Returns page ID."""
        page = self._post("/pages", {
            "parent": {"database_id": settings.notion_projects_db_id},
            "properties": {
                "Name": {"title": [{"text": {"content": prd["title"]}}]},
                "Sprint": {"rich_text": [{"text": {"content": sprint_week}}]},
                "Status": {"select": {"name": "Draft"}},
                "Type": {"select": {"name": "PRD"}},
            },
            "children": [
                self._paragraph(f"Backlog item: {prd.get('backlog_item_id', 'N/A')}"),
                self._heading("User Stories"),
                *[
                    self._paragraph(
                        f"**{s['id']}** — {s['title']}\n"
                        f"Como {s['as_a']}, quero {s['i_want']}, para que {s['so_that']}\n"
                        f"Esforço: {s['effort_points']}p  |  MVP: {'✓' if s.get('is_mvp') else '✗'}"
                    )
                    for s in prd.get("stories", [])
                ],
            ],
        })
        return page["id"]

    def create_architecture_page(self, sprint_week: str, arch: dict[str, Any]) -> str:
        """Create an architecture doc page in the Projects database. Returns page ID."""
        risks_text = "\n".join(
            f"- **{r['risk']}**: {r['mitigation']}" for r in arch.get("risks", [])
        )
        contracts_text = "\n".join(
            f"- `{c['method']} {c['path']}`" for c in arch.get("api_contracts", [])
        )
        page = self._post("/pages", {
            "parent": {"database_id": settings.notion_projects_db_id},
            "properties": {
                "Name": {"title": [{"text": {"content": f"Arch: {arch['title']}"}}]},
                "Sprint": {"rich_text": [{"text": {"content": sprint_week}}]},
                "Status": {"select": {"name": "Draft"}},
                "Type": {"select": {"name": "Architecture"}},
            },
            "children": [
                self._heading("Stack Decisions"),
                self._paragraph(json.dumps(arch.get("stack_decisions", {}), indent=2)),
                self._heading("Data Model (DDL)"),
                self._code_block(arch.get("data_model", ""), language="sql"),
                self._heading("API Contracts"),
                self._paragraph(contracts_text or "N/A"),
                self._heading("Implementation Order"),
                self._paragraph(
                    "\n".join(f"{i+1}. {s}" for i, s in enumerate(arch.get("implementation_order", [])))
                ),
                self._heading("Risks"),
                self._paragraph(risks_text or "Nenhum risco identificado."),
                self._heading("Instructions for Dev Agent"),
                self._paragraph(arch.get("dev_instructions", "")),
            ],
        })
        return page["id"]

    def create_qa_report_page(self, sprint_week: str, report: dict[str, Any]) -> str:
        """Create a QA report page. Returns page ID."""
        results_text = "\n".join(
            f"- {'✅' if r['passed'] else '❌'} {r['criterion']}"
            + (f"\n  → {r.get('notes', '')}" if r.get("notes") else "")
            for r in report.get("results", [])
        )
        page = self._post("/pages", {
            "parent": {"database_id": settings.notion_projects_db_id},
            "properties": {
                "Name": {"title": [{"text": {"content": f"QA: {report.get('title', sprint_week)}"}}]},
                "Sprint": {"rich_text": [{"text": {"content": sprint_week}}]},
                "Status": {"select": {"name": "QA Review"}},
                "Type": {"select": {"name": "QA Report"}},
            },
            "children": [
                self._heading("Resultado"),
                self._paragraph(f"**Aprovado:** {'Sim' if report.get('approved') else 'Nao'}"),
                self._heading("Criterios de Aceite"),
                self._paragraph(results_text),
                self._heading("Observacoes"),
                self._paragraph(report.get("notes", "")),
            ],
        })
        return page["id"]

    def create_deploy_page(self, sprint_week: str, deploy: dict[str, Any]) -> str:
        """Create a deploy record page. Returns page ID."""
        page = self._post("/pages", {
            "parent": {"database_id": settings.notion_projects_db_id},
            "properties": {
                "Name": {"title": [{"text": {"content": f"Deploy: {deploy.get('service', '?')} {sprint_week}"}}]},
                "Sprint": {"rich_text": [{"text": {"content": sprint_week}}]},
                "Status": {"select": {"name": deploy.get("status", "Done")}},
                "Type": {"select": {"name": "Deploy"}},
            },
            "children": [
                self._heading("Deploy Info"),
                self._paragraph(
                    f"Servico: {deploy.get('service', '?')}\n"
                    f"Ambiente: {deploy.get('environment', 'production')}\n"
                    f"URL: {deploy.get('url', 'N/A')}\n"
                    f"Status: {deploy.get('status', '?')}"
                ),
                self._heading("Logs"),
                self._paragraph(deploy.get("logs", "Sem logs.")),
            ],
        })
        return page["id"]

    def create_content_page(self, sprint_week: str, piece: dict[str, Any]) -> str:
        """Create a marketing content page for review. Returns page ID."""
        threads_text = "\n\n---\n\n".join(piece.get("threads_posts", []))
        page = self._post("/pages", {
            "parent": {"database_id": settings.notion_projects_db_id},
            "properties": {
                "Name": {"title": [{"text": {"content": f"Content: {piece.get('feature_title', sprint_week)}"}}]},
                "Sprint": {"rich_text": [{"text": {"content": sprint_week}}]},
                "Status": {"select": {"name": "Review"}},
                "Type": {"select": {"name": "Marketing"}},
            },
            "children": [
                self._heading("LinkedIn Post"),
                self._paragraph(piece.get("linkedin_post", "")),
                self._heading("Threads"),
                self._paragraph(threads_text),
            ],
        })
        return page["id"]

    def update_page_status(self, page_id: str, status: str) -> None:
        self._patch(f"/pages/{page_id}", {"properties": {"Status": {"select": {"name": status}}}})

    # ------------------------------------------------------------------
    # Helpers for property parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _text(prop: dict[str, Any] | None) -> str:
        if not prop:
            return ""
        rich_text = prop.get("rich_text") or prop.get("title") or []
        return "".join(t.get("plain_text", "") for t in rich_text)

    @staticmethod
    def _select(prop: dict[str, Any] | None) -> str:
        if not prop:
            return ""
        sel = prop.get("select") or {}
        return sel.get("name", "")

    @staticmethod
    def _multi_select(prop: dict[str, Any] | None) -> list[str]:
        if not prop:
            return []
        return [s["name"] for s in (prop.get("multi_select") or [])]

    @staticmethod
    def _number(prop: dict[str, Any] | None) -> int | None:
        if not prop:
            return None
        return prop.get("number")

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
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
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
