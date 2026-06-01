"""Integration tests for pipeline stage wiring (mocked agents)."""

from unittest.mock import MagicMock, patch

import pytest


def test_pipeline_stops_at_ceo_gate(sample_ceo_output: dict) -> None:
    """Pipeline should stop when CEO gate is rejected."""
    rejected_output = {**sample_ceo_output, "human_approved": False}

    with patch("orchestrator.agents.ceo_agent.CEOAgent") as MockCEO:
        MockCEO.return_value.run.return_value = rejected_output
        with patch("orchestrator.agents.pm_agent.PMAgent") as MockPM:
            from orchestrator.pipeline import run_pipeline
            run_pipeline()
            MockPM.assert_not_called()


def test_pipeline_calls_pm_after_ceo_approval(
    sample_ceo_output: dict, sample_pm_output: dict
) -> None:
    """PM Agent should be called when CEO gate is approved."""
    rejected_pm = {**sample_pm_output, "human_approved": False}

    with (
        patch("orchestrator.agents.ceo_agent.CEOAgent") as MockCEO,
        patch("orchestrator.agents.pm_agent.PMAgent") as MockPM,
        patch("orchestrator.agents.architect_agent.ArchitectAgent") as MockArch,
    ):
        MockCEO.return_value.run.return_value = sample_ceo_output
        MockPM.return_value.run.return_value = rejected_pm
        from orchestrator.pipeline import run_pipeline
        run_pipeline()
        MockPM.return_value.run.assert_called_once()
        MockArch.assert_not_called()


def test_pipeline_stops_at_qa_gate(
    sample_ceo_output: dict, sample_pm_output: dict
) -> None:
    """DevOps Agent should not run when QA gate is rejected."""
    mock_arch_output = {"sprint": "2026-W23", "architectures": []}
    mock_dev_output = {"sprint": "2026-W23", "implementations": []}
    mock_qa_output = {"sprint": "2026-W23", "approved": True, "human_approved": False, "reports": []}

    with (
        patch("orchestrator.agents.ceo_agent.CEOAgent") as MockCEO,
        patch("orchestrator.agents.pm_agent.PMAgent") as MockPM,
        patch("orchestrator.agents.architect_agent.ArchitectAgent") as MockArch,
        patch("orchestrator.agents.dev_agent.DevAgent") as MockDev,
        patch("orchestrator.agents.qa_agent.QAAgent") as MockQA,
        patch("orchestrator.agents.devops_agent.DevOpsAgent") as MockDevOps,
    ):
        MockCEO.return_value.run.return_value = sample_ceo_output
        MockPM.return_value.run.return_value = sample_pm_output
        MockArch.return_value.run.return_value = mock_arch_output
        MockDev.return_value.run.return_value = mock_dev_output
        MockQA.return_value.run.return_value = mock_qa_output

        from orchestrator.pipeline import run_pipeline
        run_pipeline()
        MockDevOps.assert_not_called()
