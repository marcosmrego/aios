"""Shared fixtures for AIOS tests."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def sample_backlog() -> list[dict[str, Any]]:
    return [
        {
            "notion_id": "abc-123",
            "title": "Dashboard de métricas do Climate",
            "description": "Visualização de temperatura, umidade e pressão em tempo real.",
            "status": "Backlog",
            "priority": "High",
            "project": "Climate",
            "effort_points": 5,
            "tags": ["frontend", "realtime"],
            "url": "https://notion.so/abc-123",
        },
        {
            "notion_id": "def-456",
            "title": "Relatório mensal GRC Flow",
            "description": "Geração automática de relatório de compliance em PDF.",
            "status": "Backlog",
            "priority": "Medium",
            "project": "GRC Flow",
            "effort_points": 3,
            "tags": ["backend", "pdf"],
            "url": "https://notion.so/def-456",
        },
    ]


@pytest.fixture
def sample_ceo_output() -> dict[str, Any]:
    return {
        "week": "2026-W23",
        "human_approved": True,
        "priorities": [
            {
                "title": "Dashboard de métricas do Climate",
                "notion_id": "abc-123",
                "business_justification": "Clientes pedem visualização em tempo real há 2 meses.",
                "estimated_effort": "5",
                "assigned_to_pm": True,
            }
        ],
        "paused": ["def-456"],
        "success_metrics": ["Dashboard acessível em produção", "Tempo de carregamento < 2s"],
        "risks": ["Integração com WebSocket pode atrasar"],
        "pm_instructions": "Foque no MVP: gráfico de temperatura das últimas 24h.",
        "slack_summary": "Plano semana 23: Dashboard Climate (5p). GRC Flow pausado.",
    }


@pytest.fixture
def sample_pm_output() -> dict[str, Any]:
    return {
        "sprint": "2026-W23",
        "human_approved": True,
        "prds": [
            {
                "notion_id": "prd-789",
                "title": "Dashboard de métricas do Climate",
                "backlog_item_id": "abc-123",
                "stories": [
                    {
                        "id": "US-001",
                        "title": "Ver temperatura em tempo real",
                        "as_a": "operador",
                        "i_want": "ver a temperatura atual no dashboard",
                        "so_that": "possa tomar decisões em tempo real",
                        "acceptance_criteria": [
                            "Temperatura exibida em °C com 1 casa decimal",
                            "Atualização automática a cada 30 segundos",
                            "Exibe dados das últimas 24h em gráfico de linha",
                        ],
                        "effort_points": 5,
                        "is_mvp": True,
                    }
                ],
                "dependencies": ["API de sensores Climate existente"],
                "architect_instructions": "Use WebSocket ou polling de 30s. PostgreSQL time-series.",
            }
        ],
        "slack_summary": "PRD Dashboard Climate criado. 1 story MVP.",
    }


@pytest.fixture
def mock_anthropic_response():
    """Mock Anthropic client to avoid real API calls in unit tests."""
    mock_content = MagicMock()
    mock_content.text = json.dumps({"week": "2026-W23", "priorities": [], "slack_summary": "test"})
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    return mock_response


@pytest.fixture
def mock_notion_client():
    """Mock Notion client."""
    with patch("tools.notion.Client") as mock:
        instance = MagicMock()
        mock.return_value = instance
        instance.databases.query.return_value = {"results": []}
        instance.pages.create.return_value = {"id": "page-mock-id"}
        yield instance


@pytest.fixture(autouse=True)
def mock_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject test environment variables so Settings() doesn't fail."""
    env_vars = {
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "NOTION_API_KEY": "secret_test",
        "NOTION_BACKLOG_DB_ID": "backlog-db-id",
        "NOTION_PROJECTS_DB_ID": "projects-db-id",
        "NOTION_SPRINTS_DB_ID": "sprints-db-id",
        "SLACK_WEBHOOK_URL": "",
        "HUMAN_IN_THE_LOOP": "false",
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    # Re-create settings with patched env
    import importlib  # noqa: PLC0415
    import orchestrator.settings as settings_module  # noqa: PLC0415
    importlib.reload(settings_module)
