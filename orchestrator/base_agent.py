"""Base class for all AIOS agents."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import anthropic
from rich.console import Console

from orchestrator.settings import settings
from tools.usage_tracker import estimate_cost, log_run

console = Console(legacy_windows=False)


class BaseAgent:
    """Shared scaffolding for every specialized agent."""

    name: str
    role: str
    model: str
    prompt_file: str
    pipeline: str = "expansao"

    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._system_prompt = self._load_prompt()

    def _load_prompt(self) -> str:
        path = Path(self.prompt_file)
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {self.prompt_file}")
        return path.read_text(encoding="utf-8")

    def _run(self, user_message: str, max_tokens: int = 4096) -> str:
        """Send a message to Claude and return the text response."""
        console.print(f"[bold blue]>> {self.name}[/] thinking...", end=" ")
        t0 = time.monotonic()
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=self._system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        duration_ms = int((time.monotonic() - t0) * 1000)
        console.print("[green]done[/]")

        usage = response.usage
        cost = estimate_cost(self.model, usage.input_tokens, usage.output_tokens)
        console.print(
            f"[dim]  tokens: {usage.input_tokens}↑ {usage.output_tokens}↓  "
            f"cost: ${cost:.4f}[/]"
        )
        try:
            log_run(
                project=self.pipeline,  # pipeline doubles as project for AIOS agents
                pipeline=self.pipeline,
                agent_name=self.name,
                model=self.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=cost,
                duration_ms=duration_ms,
            )
        except Exception:
            pass  # tracking must never break agent execution

        return response.content[0].text  # type: ignore[union-attr]

    def _parse_json_output(self, text: str) -> dict[str, Any]:
        """Extract the first JSON block from the agent's response."""
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError(f"No JSON found in agent output:\n{text[:500]}")
        return json.loads(text[start:end])

    def _save_output(self, data: dict[str, Any], filename: str) -> Path:
        """Persist agent output to the outputs directory."""
        out_dir = Path(settings.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / filename
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[dim]Saved: {path}[/]")
        return path

    def _await_human_approval(self, gate_id: str, context_summary: str) -> bool:
        """Block until a human approves or rejects the gate (CLI mode)."""
        if not settings.human_in_the_loop:
            return True
        console.print(f"\n[yellow bold][GATE] Human gate: {gate_id}[/]")
        console.print(f"[dim]{context_summary}[/]\n")
        answer = console.input("[bold]Aprovar? (s/n): [/]").strip().lower()
        return answer in ("s", "sim", "y", "yes")
