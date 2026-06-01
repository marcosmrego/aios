"""Importador — copia paginas de reunioes existentes do Notion para o CWI Meetings database."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import httpx
from rich.console import Console
from rich.table import Table

from orchestrator.settings import settings

console = Console(legacy_windows=False)

_NOTION_VERSION = "2022-06-28"
_BASE_URL = "https://api.notion.com/v1"

# Padroes de titulo que indicam reunioes/atas
_MEETING_PATTERNS = [
    r"reuni",
    r"\bata\b",
    r"alinhamento",
    r"kick.?off",
    r"daily",
    r"sprint",
    r"retrospectiva",
    r"planning",
    r"review",
    r"standup",
]
_MEETING_RE = re.compile("|".join(_MEETING_PATTERNS), re.IGNORECASE)

# Detectar tipo pelo titulo
_ATA_RE    = re.compile(r"\bata\b", re.IGNORECASE)
_TRANS_RE  = re.compile(r"transcri|recording|gravac", re.IGNORECASE)


def importar_reunioes(dry_run: bool = False, query: str = "reuniao") -> None:
    """
    Busca paginas de reunioes no Notion e importa para CWI Meetings.

    dry_run=True apenas lista o que seria importado, sem criar nada.
    """
    http = httpx.Client(
        base_url=_BASE_URL,
        headers={
            "Authorization": f"Bearer {settings.notion_api_key}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
        },
        timeout=30,
    )

    console.rule("[bold cyan]Importador CWI Meetings[/]")

    # 1. Buscar paginas candidatas
    pages = _search_pages(http, query)
    console.print(f"[dim]{len(pages)} paginas encontradas para query '{query}'[/]\n")

    # 2. Filtrar as que parecem reunioes/atas e nao sao dos nossos databases
    our_db_ids = {
        settings.cwi_meetings_db_id,
        settings.cwi_reports_db_id,
        settings.cwi_backlog_db_id,
        settings.cwi_arquivo_db_id,
        settings.notion_backlog_db_id,
        settings.notion_projects_db_id,
        settings.notion_sprints_db_id,
        settings.cwi_transcriptions_db_id,
    }

    candidates = []
    for p in pages:
        title = _get_title(p)
        if not _MEETING_RE.search(title):
            continue
        parent = p.get("parent", {})
        parent_id = parent.get("database_id") or parent.get("page_id") or ""
        if parent_id.replace("-", "") in {d.replace("-", "") for d in our_db_ids if d}:
            continue
        candidates.append(p)

    if not candidates:
        console.print("[yellow]Nenhuma pagina de reuniao encontrada fora dos nossos databases.[/]")
        return

    # 3. Exibir tabela de candidatas
    table = Table(title=f"Paginas para importar ({len(candidates)})")
    table.add_column("Titulo", style="bold", max_width=55)
    table.add_column("Criada em", width=12)
    table.add_column("Tipo", width=12)

    for p in candidates:
        title = _get_title(p)
        created = p.get("created_time", "")[:10]
        tipo = _detect_tipo(title)
        table.add_row(_safe(title), created, tipo)

    console.print(table)

    if dry_run:
        console.print("\n[yellow]Modo dry-run — nada foi importado.[/]")
        return

    # 4. Importar
    imported = 0
    skipped  = 0

    for p in candidates:
        title  = _get_title(p)
        tipo   = _detect_tipo(title)
        url    = p.get("url", "")
        created = p.get("created_time", "")[:10] or date.today().isoformat()

        # Ler blocos de conteudo da pagina original
        blocks = _fetch_blocks(http, p["id"])

        try:
            _create_meeting_entry(http, title, created, tipo, url, blocks)
            console.print(f"  [green]Importado:[/] {title[:60]}")
            imported += 1
        except Exception as e:
            console.print(f"  [red]Erro:[/] {title[:50]} — {e}")
            skipped += 1

    console.rule()
    console.print(f"[bold green]{imported} importados[/]  [dim]{skipped} com erro[/]")


def _search_pages(http: httpx.Client, query: str) -> list[dict[str, Any]]:
    """Search Notion for pages matching query."""
    all_pages: list[dict[str, Any]] = []
    cursor = None

    while True:
        body: dict[str, Any] = {
            "query": query,
            "filter": {"value": "page", "property": "object"},
            "page_size": 50,
        }
        if cursor:
            body["start_cursor"] = cursor

        r = http.post("/search", json=body)
        r.raise_for_status()
        data = r.json()
        all_pages.extend(data.get("results", []))

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return all_pages


def _fetch_blocks(http: httpx.Client, page_id: str, max_blocks: int = 50) -> list[dict[str, Any]]:
    """Fetch top-level blocks from a page."""
    r = http.get(f"/blocks/{page_id}/children", params={"page_size": max_blocks})
    if r.status_code != 200:
        return []
    return r.json().get("results", [])


_SAFE_BLOCK_TYPES = {
    "paragraph", "heading_1", "heading_2", "heading_3",
    "bulleted_list_item", "numbered_list_item", "quote",
    "callout", "divider", "code",
}


def _create_meeting_entry(
    http: httpx.Client,
    title: str,
    created: str,
    tipo: str,
    origem_url: str,
    blocks: list[dict[str, Any]],
) -> None:
    """Create a page in CWI Meetings database."""
    # Source callout always at the top
    children: list[dict[str, Any]] = [{
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [{"type": "text", "text": {"content": f"Importado de: {origem_url or 'Notion'}"}}],
            "color": "blue_background",
        },
    }]

    for b in blocks[:40]:
        btype = b.get("type", "")

        # Only copy block types the API accepts safely
        if btype in _SAFE_BLOCK_TYPES:
            content = b.get(btype, {})
            rt = content.get("rich_text", [])
            if btype == "divider":
                children.append({"object": "block", "type": "divider", "divider": {}})
            elif rt:
                safe_rt = [
                    {"type": "text", "text": {"content": t.get("plain_text", "")}}
                    for t in rt if t.get("plain_text")
                ]
                if safe_rt:
                    block_content: dict[str, Any] = {"rich_text": safe_rt}
                    if btype == "code" and content.get("language"):
                        block_content["language"] = content["language"]
                    children.append({"object": "block", "type": btype, btype: block_content})
        elif btype in ("table", "toggle", "column_list", "synced_block"):
            # For complex blocks, extract text as a paragraph
            inner = b.get(btype, {})
            rt = inner.get("rich_text", [])
            text = "".join(t.get("plain_text", "") for t in rt)
            if text.strip():
                children.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]},
                })

    http.post("/pages", json={
        "parent": {"database_id": settings.cwi_meetings_db_id},
        "properties": {
            "Name":   {"title": [{"text": {"content": title[:255]}}]},
            "Date":   {"date": {"start": created}},
            "Status": {"select": {"name": "Importado"}},
            "Tipo":   {"select": {"name": tipo}},
            "Origem": {"url": origem_url or None},
        },
        "children": children[:100],
    }).raise_for_status()


def _safe(text: str) -> str:
    """Strip characters that can't be displayed in cp1252 terminals."""
    return text.encode("cp1252", errors="replace").decode("cp1252")


def _get_title(page: dict[str, Any]) -> str:
    props = page.get("properties", {})
    for key in ["title", "Title", "Name", "Titulo"]:
        t = props.get(key, {})
        rt = t.get("title") or t.get("rich_text") or []
        text = "".join(x.get("plain_text", "") for x in rt)
        if text:
            return text
    return "(sem titulo)"


def _detect_tipo(title: str) -> str:
    if _ATA_RE.search(title):
        return "ATA"
    if _TRANS_RE.search(title):
        return "Transcricao"
    return "Reuniao"
