"""AIOS MCP Server — exposes AIOS capabilities as tools for Claude Desktop."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Ensure project root is in path when running as installed script
_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# Load .env before importing settings
from dotenv import load_dotenv  # noqa: E402
load_dotenv(_root / ".env")

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("AIOS — Expansão AI OS")


# ── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
def generate_spec(description: str, pipeline: str = "Expansão AI") -> str:
    """Gera especificação funcional a partir de uma descrição em linguagem natural.

    Cria a spec no Notion (Status=Rascunho), gera .docx em documents/specs/ e
    diagrama de fluxo em documents/diagrams/.

    Args:
        description: Descrição do sistema ou feature a especificar.
        pipeline: "Expansão AI" (padrão) ou "CWI".

    Returns:
        Resumo com link do Notion e arquivos gerados.
    """
    try:
        from orchestrator.agents.spec_agent import SpecAgent  # noqa: PLC0415
        result = SpecAgent().run(
            input_text=description,
            pipeline=pipeline,
            origem="Texto Livre",
        )
        spec = result.get("spec", {})
        notion_id = result.get("notion_page_id", "")
        diagrams = result.get("diagrams", [])

        lines = [f"✅ Spec gerada: **{spec.get('titulo', 'Sem título')}**"]
        if notion_id:
            lines.append(f"📄 Notion: https://notion.so/{notion_id.replace('-', '')}")
        lines.append(f"📝 Word: {result.get('docx', '')}")
        if diagrams:
            lines.append(f"🗂️ Diagrama: {diagrams[0]}")
        lines.append(f"\nCasos de uso: {len(spec.get('casos_de_uso', []))}")
        lines.append(f"Regras de negócio: {len(spec.get('regras_de_negocio', []))}")
        perguntas = spec.get("perguntas_em_aberto", [])
        if perguntas:
            lines.append(f"\n⚠️ Perguntas em aberto:\n" + "\n".join(f"- {p}" for p in perguntas))
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Erro ao gerar spec: {e}"


@mcp.tool()
def generate_diagram(description: str) -> str:
    """Gera diagrama de fluxo (.drawio + Mermaid) a partir de uma descrição de processo.

    Args:
        description: Descrição do processo ou fluxo a diagramar.

    Returns:
        Caminhos dos arquivos gerados (.drawio e .md com Mermaid).
    """
    try:
        from orchestrator.agents.diagram_agent import DiagramAgent  # noqa: PLC0415
        paths = DiagramAgent().run(input_text=description)
        return (
            f"✅ Diagrama gerado:\n"
            f"🗂️ draw.io: {paths[0]}\n"
            f"📝 Mermaid: {paths[1]}"
        )
    except Exception as e:
        return f"❌ Erro ao gerar diagrama: {e}"


@mcp.tool()
def list_specs(pipeline: str = "") -> str:
    """Lista specs no Notion aguardando revisão ou aprovação.

    Args:
        pipeline: Filtrar por "Expansão AI" ou "CWI". Vazio retorna todos.

    Returns:
        Lista com título, pipeline, status e link de cada spec.
    """
    try:
        from tools.notion import NotionClient  # noqa: PLC0415
        from orchestrator.settings import settings  # noqa: PLC0415

        if not settings.notion_specs_db_id:
            return "⚠️ NOTION_SPECS_DB_ID não configurado."

        notion = NotionClient()

        # Query all specs (not just approved)
        body: dict[str, Any] = {
            "sorts": [{"timestamp": "created_time", "direction": "descending"}],
            "page_size": 20,
        }
        if pipeline:
            body["filter"] = {"property": "Pipeline", "select": {"equals": pipeline}}

        results = notion._post(
            f"/databases/{settings.notion_specs_db_id}/query", body
        ).get("results", [])

        if not results:
            return "📭 Nenhuma spec encontrada."

        lines = [f"📋 **Specs{f' — {pipeline}' if pipeline else ''}** ({len(results)} encontradas)\n"]
        for page in results:
            props = page.get("properties", {})
            titulo = notion._text(props.get("Name"))
            status = notion._select(props.get("Status"))
            pipe = notion._select(props.get("Pipeline"))
            aprovado = props.get("Aprovado", {}).get("checkbox", False)
            url = f"https://notion.so/{page['id'].replace('-', '')}"
            icon = "✅" if aprovado else "⏳"
            lines.append(f"{icon} **{titulo}** — {pipe} — {status}")
            lines.append(f"   {url}")

        return "\n".join(lines)
    except Exception as e:
        return f"❌ Erro ao listar specs: {e}"


@mcp.tool()
def run_agent(agent: str, pipeline: str = "expansao", input_text: str = "") -> str:
    """Aciona qualquer agente do AIOS diretamente.

    Args:
        agent: Nome do agente.
          Expansão AI: ceo, pm, architect, dev, qa, devops, marketing, spec, diagram
          CWI: meeting-secretary, pmo, agile-coach, product, executive-reporting, spec, diagram
        pipeline: "expansao" (padrão) ou "cwi".
        input_text: Texto livre de input para o agente (quando aplicável).

    Returns:
        Resumo do output gerado pelo agente.
    """
    try:
        import os  # noqa: PLC0415
        os.environ["HUMAN_IN_THE_LOOP"] = "false"

        result: Any = None

        if agent == "spec":
            from orchestrator.agents.spec_agent import SpecAgent  # noqa: PLC0415
            pipe_label = "CWI" if pipeline == "cwi" else "Expansão AI"
            result = SpecAgent().run(input_text=input_text, pipeline=pipe_label)
            spec = result.get("spec", {})
            return f"✅ Spec: {spec.get('titulo', '?')} — {len(spec.get('casos_de_uso', []))} UCs"

        if agent == "diagram":
            from orchestrator.agents.diagram_agent import DiagramAgent  # noqa: PLC0415
            paths = DiagramAgent().run(input_text=input_text)
            return f"✅ Diagrama gerado: {paths[0]}"

        if pipeline == "cwi":
            if agent == "meeting-secretary":
                from orchestrator.agents_cwi.meeting_secretary_agent import MeetingSecretaryAgent  # noqa: PLC0415
                result = MeetingSecretaryAgent().run(transcript=input_text)
            elif agent == "pmo":
                from orchestrator.agents_cwi.pmo_agent import PMOAgent  # noqa: PLC0415
                result = PMOAgent().run(extra_context=input_text)
            elif agent == "agile-coach":
                from orchestrator.agents_cwi.agile_coach_agent import AgileCoachAgent  # noqa: PLC0415
                result = AgileCoachAgent().run(extra_context=input_text)
            elif agent == "product":
                from orchestrator.agents_cwi.product_agent import ProductAgent  # noqa: PLC0415
                result = ProductAgent().run(demands_text=input_text)
            elif agent == "executive-reporting":
                from orchestrator.agents_cwi.executive_reporting_agent import ExecutiveReportingAgent  # noqa: PLC0415
                result = ExecutiveReportingAgent().run(extra_context=input_text)
            else:
                return f"❌ Agente CWI desconhecido: {agent}"
        else:
            if agent == "ceo":
                from orchestrator.agents.ceo_agent import CEOAgent  # noqa: PLC0415
                result = CEOAgent().run(extra_context=input_text)
            elif agent == "pm":
                from orchestrator.agents.pm_agent import PMAgent  # noqa: PLC0415
                result = PMAgent().run(spec_data={"_raw_text": input_text} if input_text else None)
            else:
                return f"❌ Agente Expansão AI '{agent}' requer outputs de etapas anteriores. Use o pipeline completo."

        if result:
            summary = result.get("slack_summary") or result.get("resumo_executivo") or result.get("titulo", "")
            return f"✅ {agent} concluído.\n{summary[:300]}"
        return f"✅ {agent} concluído."

    except Exception as e:
        return f"❌ Erro ao rodar {agent}: {e}"


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
