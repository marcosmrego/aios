"""Diagram Agent — generates .drawio and Mermaid diagrams from specs or free text."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Any

from rich.console import Console

from orchestrator.base_agent import BaseAgent
from orchestrator.settings import settings

console = Console(legacy_windows=False)

DIAGRAMS_DIR = Path("documents/diagrams")

# ── draw.io node dimensions ───────────────────────────────────────────────────
_W_ACTION   = 160
_H_ACTION   = 60
_W_DECISION = 120
_H_DECISION = 80
_W_TERM     = 120
_H_TERM     = 40
_COL_GAP    = 200
_ROW_GAP    = 100

_STYLES = {
    "inicio":   "rounded=1;whiteSpace=wrap;fillColor=#d5e8d4;strokeColor=#82b366;",
    "fim":      "rounded=1;whiteSpace=wrap;fillColor=#f8cecc;strokeColor=#b85450;",
    "acao":     "rounded=0;whiteSpace=wrap;",
    "decisao":  "rhombus;whiteSpace=wrap;",
    "sistema":  "rounded=0;whiteSpace=wrap;fillColor=#dae8fc;strokeColor=#6c8ebf;",
}


class DiagramAgent(BaseAgent):
    name = "Diagram Agent"
    role = "Analista de Fluxos e Diagramas"
    model: str = settings.architect_model
    prompt_file = "agents/prompts/diagram.md"

    def __init__(self) -> None:
        self.model = settings.architect_model
        super().__init__()

    def run(
        self,
        spec: dict[str, Any] | None = None,
        input_text: str = "",
        input_file: str = "",
        label: str = "diagram",
    ) -> list[Path]:
        """Generate .drawio + .md (Mermaid) files. Returns list of generated paths."""
        console.rule("[bold]Diagram Agent")

        # 1. Build prompt input
        if spec:
            source = json.dumps(spec, ensure_ascii=False, indent=2)
            label = spec.get("titulo", label).replace(" ", "_").lower()[:40]
        elif input_text:
            source = input_text
        elif input_file:
            source = Path(input_file).read_text(encoding="utf-8")
            label = Path(input_file).stem[:40]
        else:
            raise ValueError("Forneça spec, input_text ou input_file.")

        # 2. Ask Claude to structure the diagram
        user_message = f"""Analise o conteudo abaixo e estruture o(s) diagrama(s) de fluxo.

CONTEUDO:
{source[:6000]}

Retorne apenas o JSON com os fluxos, sem texto adicional."""

        response_text = self._run(user_message, max_tokens=4096)
        diagram_data = self._parse_json_output(response_text)

        # 3. Generate files
        today = date.today().strftime("%Y_%m_%d")
        base_name = f"diagram_{label}_{today}"
        DIAGRAMS_DIR.mkdir(parents=True, exist_ok=True)

        drawio_path = self._build_drawio(diagram_data, base_name)
        mermaid_path = self._build_mermaid(diagram_data, base_name)

        console.print(f"[green]draw.io:[/] {drawio_path}")
        console.print(f"[green]Mermaid:[/] {mermaid_path}")
        return [drawio_path, mermaid_path]

    # ── draw.io builder ───────────────────────────────────────────────────────

    def _build_drawio(self, data: dict[str, Any], base_name: str) -> Path:
        mxfile = ET.Element("mxfile")
        diagram_el = ET.SubElement(mxfile, "diagram", name=data.get("titulo", "Fluxo"))
        model = ET.SubElement(diagram_el, "mxGraphModel",
                              dx="1422", dy="762", grid="1", gridSize="10",
                              guides="1", tooltips="1", connect="1", arrows="1",
                              fold="1", page="1", pageScale="1",
                              pageWidth="1169", pageHeight="827", math="0", shadow="0")
        root = ET.SubElement(model, "root")
        ET.SubElement(root, "mxCell", id="0")
        ET.SubElement(root, "mxCell", id="1", parent="0")

        cell_id = 2
        for fi, fluxo in enumerate(data.get("fluxos", [])):
            x_offset = fi * (_W_ACTION + _COL_GAP * 3)
            node_positions: dict[str, tuple[int, int]] = {}
            node_ids: dict[str, str] = {}

            # Place nodes vertically
            for ni, no in enumerate(fluxo.get("nos", [])):
                ntype = no.get("tipo", "acao")
                w = _W_DECISION if ntype == "decisao" else _W_ACTION if ntype in ("acao", "sistema") else _W_TERM
                h = _H_DECISION if ntype == "decisao" else _H_TERM if ntype in ("inicio", "fim") else _H_ACTION
                x = x_offset
                y = ni * (_H_ACTION + _ROW_GAP)
                node_positions[no["id"]] = (x, y)

                cid = str(cell_id)
                node_ids[no["id"]] = cid
                cell_id += 1

                style = _STYLES.get(ntype, _STYLES["acao"])
                label_text = no.get("label", "")
                if no.get("ator") and ntype not in ("inicio", "fim"):
                    label_text = f"[{no['ator']}]\n{label_text}"

                cell = ET.SubElement(root, "mxCell",
                                     id=cid, value=label_text, style=style,
                                     vertex="1", parent="1")
                ET.SubElement(cell, "mxGeometry",
                              x=str(x), y=str(y), width=str(w), height=str(h),
                              **{"as": "geometry"})

            # Draw edges
            for edge in fluxo.get("arestas", []):
                src = node_ids.get(edge["de"])
                tgt = node_ids.get(edge["para"])
                if not src or not tgt:
                    continue
                cid = str(cell_id)
                cell_id += 1
                cell = ET.SubElement(root, "mxCell",
                                     id=cid, value=edge.get("label", ""),
                                     style="edgeStyle=orthogonalEdgeStyle;",
                                     edge="1", source=src, target=tgt, parent="1")
                ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})

        path = DIAGRAMS_DIR / f"{base_name}.drawio"
        ET.ElementTree(mxfile).write(str(path), encoding="utf-8", xml_declaration=True)
        return path

    # ── Mermaid builder ───────────────────────────────────────────────────────

    def _build_mermaid(self, data: dict[str, Any], base_name: str) -> Path:
        lines: list[str] = [f"# {data.get('titulo', 'Diagrama')}\n"]

        for fluxo in data.get("fluxos", []):
            lines.append(f"## {fluxo.get('nome', 'Fluxo')}\n")
            lines.append("```mermaid")
            lines.append("flowchart TD")

            for no in fluxo.get("nos", []):
                nid = no["id"]
                label = no.get("label", nid).replace('"', "'")
                ntype = no.get("tipo", "acao")
                if ntype in ("inicio", "fim"):
                    lines.append(f'    {nid}(("{label}"))')
                elif ntype == "decisao":
                    lines.append(f'    {nid}{{"{label}"}}')
                else:
                    lines.append(f'    {nid}["{label}"]')

            for edge in fluxo.get("arestas", []):
                lbl = edge.get("label", "")
                arrow = f"-- {lbl} -->" if lbl else "-->"
                lines.append(f"    {edge['de']} {arrow} {edge['para']}")

            lines.append("```\n")

        path = DIAGRAMS_DIR / f"{base_name}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
