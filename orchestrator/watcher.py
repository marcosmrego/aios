"""Watcher — polls Notion for new meeting transcriptions and triggers agents automatically."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx
from rich.console import Console

from orchestrator.settings import settings

console = Console(legacy_windows=False)

_NOTION_VERSION = "2022-06-28"
_BASE_URL = "https://api.notion.com/v1"

# Property name used to track processed pages in Projetos DB
_PROCESSED_PROP = "Resumo por IA"


def watch_cwi_meetings(interval_seconds: int = 0) -> None:
    """
    Poll the Projetos database for pages containing a 'transcription' block
    with status 'notes_ready' that haven't been processed yet.
    """
    interval = interval_seconds or settings.cwi_watch_interval_seconds

    http = httpx.Client(
        base_url=_BASE_URL,
        headers={
            "Authorization": f"Bearer {settings.notion_api_key}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
        },
        timeout=30,
    )

    console.rule("[bold cyan]CWI Watcher iniciado[/]")
    console.print(f"[dim]Monitorando: database Projetos ({settings.cwi_projetos_db_id})[/]")
    console.print(f"[dim]Intervalo: {interval}s | Ctrl+C para parar[/]\n")

    while True:
        try:
            _check_projetos(http)
        except KeyboardInterrupt:
            console.print("\n[yellow]Watcher encerrado pelo usuario.[/]")
            break
        except Exception as e:
            console.print(f"[red]Erro no ciclo do watcher: {e}[/]")

        try:
            console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')} — aguardando {interval}s...[/]")
            time.sleep(interval)
        except KeyboardInterrupt:
            console.print("\n[yellow]Watcher encerrado pelo usuario.[/]")
            break


def _check_projetos(http: httpx.Client) -> None:
    """Check Projetos DB for pages with unprocessed transcription blocks."""
    # Query pages where Resumo por IA is empty (not yet processed)
    r = http.post(f"/databases/{settings.cwi_projetos_db_id}/query", json={
        "filter": {
            "property": _PROCESSED_PROP,
            "rich_text": {"is_empty": True},
        },
        "sorts": [{"timestamp": "created_time", "direction": "descending"}],
        "page_size": 20,
    })
    r.raise_for_status()
    pages = r.json().get("results", [])

    for page in pages:
        trans = _find_transcription_block(http, page["id"])
        if trans and trans.get("transcription", {}).get("status") == "notes_ready":
            _process_transcription(http, page, trans)


def _find_transcription_block(http: httpx.Client, page_id: str) -> dict[str, Any] | None:
    """Return the first transcription block in a page, or None."""
    r = http.get(f"/blocks/{page_id}/children", params={"page_size": 20})
    if r.status_code != 200:
        return None
    for block in r.json().get("results", []):
        if block.get("type") == "transcription":
            return block
    return None


def _process_transcription(http: httpx.Client, page: dict, trans_block: dict) -> None:
    """Run Meeting Secretary on a transcription block and mark page as processed."""
    props = page.get("properties", {})
    titulo_rt = props.get("Nome do projeto", {}).get("title", [])
    titulo = "".join(t.get("plain_text", "") for t in titulo_rt) or "Reuniao sem titulo"

    console.print(f"\n[bold]Transcricao encontrada:[/] {titulo}")

    trans = trans_block.get("transcription", {})
    children_ids = trans.get("children", {})
    transcript_block_id = children_ids.get("transcript_block_id")
    summary_block_id    = children_ids.get("summary_block_id")

    transcript_text = _extract_block_text(http, transcript_block_id) if transcript_block_id else ""
    summary_text    = _extract_block_text(http, summary_block_id)    if summary_block_id    else ""

    full_text = ""
    if summary_text:
        full_text += f"=== RESUMO / ACOES (gerado pelo Notion) ===\n{summary_text}\n\n"
    if transcript_text:
        full_text += f"=== TRANSCRICAO COMPLETA ===\n{transcript_text}"

    if not full_text.strip():
        console.print("[yellow]Transcricao vazia — ignorando.[/]")
        return

    console.print(f"[dim]Conteudo: {len(full_text)} chars[/]")

    try:
        from orchestrator.agents_cwi.meeting_secretary_agent import MeetingSecretaryAgent  # noqa: PLC0415
        agent = MeetingSecretaryAgent()
        output = agent.run(transcript=full_text)

        # Mark page as processed — write summary to Resumo por IA
        resumo = output.get("resumo_executivo", "Processado pelo Meeting Secretary Agent.")
        http.patch(f"/pages/{page['id']}", json={
            "properties": {
                _PROCESSED_PROP: {"rich_text": [{"text": {"content": resumo[:2000]}}]},
            }
        })
        console.print(f"[green]Concluido: {titulo}[/]")

    except ValueError as e:
        console.print(f"[yellow]Sem conteudo util em '{titulo}': {e}[/]")
        _mark_processed(http, page["id"], "Sem conteudo para processar.")
    except Exception as e:
        console.print(f"[red]Erro ao processar '{titulo}': {e}[/]")


def _extract_block_text(http: httpx.Client, block_id: str, depth: int = 0) -> str:
    """Recursively extract plain text from a block and its children."""
    if depth > 3:
        return ""
    r = http.get(f"/blocks/{block_id}/children", params={"page_size": 100})
    if r.status_code != 200:
        return ""
    lines = []
    for b in r.json().get("results", []):
        btype = b.get("type", "")
        content = b.get(btype, {})
        rt = content.get("rich_text", [])
        text = "".join(t.get("plain_text", "") for t in rt)
        if text.strip():
            lines.append(text)
        if b.get("has_children"):
            child_text = _extract_block_text(http, b["id"], depth + 1)
            if child_text:
                lines.append(child_text)
    return "\n".join(lines)


def _mark_processed(http: httpx.Client, page_id: str, note: str) -> None:
    try:
        http.patch(f"/pages/{page_id}", json={
            "properties": {
                _PROCESSED_PROP: {"rich_text": [{"text": {"content": note}}]},
            }
        })
    except Exception:
        pass
