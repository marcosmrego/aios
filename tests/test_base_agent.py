"""Tests for BaseAgent shared scaffolding."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.base_agent import BaseAgent


class ConcreteAgent(BaseAgent):
    name = "Test Agent"
    role = "Tester"
    model = "claude-haiku-4-5-20251001"
    prompt_file = "agents/prompts/ceo.md"  # reuse existing file


@pytest.fixture
def agent() -> ConcreteAgent:
    with patch("orchestrator.base_agent.anthropic.Anthropic"):
        return ConcreteAgent()


def test_load_prompt_reads_file(agent: ConcreteAgent) -> None:
    assert len(agent._system_prompt) > 100


def test_load_prompt_raises_for_missing_file() -> None:
    with patch("orchestrator.base_agent.anthropic.Anthropic"):
        bad_agent = ConcreteAgent.__new__(ConcreteAgent)
        bad_agent.prompt_file = "nonexistent/prompt.md"
        with pytest.raises(FileNotFoundError):
            bad_agent._load_prompt()


def test_parse_json_output_extracts_json(agent: ConcreteAgent) -> None:
    text = 'Some text before {"key": "value", "num": 42} and after'
    result = agent._parse_json_output(text)
    assert result == {"key": "value", "num": 42}


def test_parse_json_output_raises_without_json(agent: ConcreteAgent) -> None:
    with pytest.raises(ValueError, match="No JSON found"):
        agent._parse_json_output("no json here at all")


def test_save_output_writes_file(agent: ConcreteAgent, tmp_path: Path) -> None:
    with patch("orchestrator.base_agent.settings") as mock_settings:
        mock_settings.output_dir = str(tmp_path)
        data = {"test": True, "value": 42}
        path = agent._save_output(data, "test_output.json")
        assert path.exists()
        assert json.loads(path.read_text()) == data


def test_await_human_approval_skips_when_disabled(agent: ConcreteAgent) -> None:
    with patch("orchestrator.base_agent.settings") as mock_settings:
        mock_settings.human_in_the_loop = False
        result = agent._await_human_approval("test-gate", "summary")
        assert result is True


def test_await_human_approval_prompts_user(agent: ConcreteAgent) -> None:
    with patch("orchestrator.base_agent.settings") as mock_settings:
        mock_settings.human_in_the_loop = True
        with patch("orchestrator.base_agent.console") as mock_console:
            mock_console.input.return_value = "s"
            result = agent._await_human_approval("test-gate", "summary")
            assert result is True
            mock_console.input.assert_called_once()


def test_await_human_approval_rejects_on_no(agent: ConcreteAgent) -> None:
    with patch("orchestrator.base_agent.settings") as mock_settings:
        mock_settings.human_in_the_loop = True
        with patch("orchestrator.base_agent.console") as mock_console:
            mock_console.input.return_value = "n"
            result = agent._await_human_approval("test-gate", "summary")
            assert result is False
