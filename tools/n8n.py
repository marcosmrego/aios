"""N8N API integration — triggers DevOps and Marketing workflows."""

from __future__ import annotations

from typing import Any

import httpx

from orchestrator.settings import settings

_TIMEOUT = 30


class N8NClient:
    """Triggers N8N workflows via REST API and polls for completion."""

    def __init__(self) -> None:
        if not settings.n8n_base_url:
            raise RuntimeError("N8N_BASE_URL not configured")
        self.base_url = settings.n8n_base_url.rstrip("/")
        self.headers = {
            "X-N8N-API-KEY": settings.n8n_api_key,
            "Content-Type": "application/json",
        }

    def trigger_workflow(self, workflow_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Trigger a workflow by ID and return the execution response."""
        url = f"{self.base_url}/api/v1/workflows/{workflow_id}/activate"
        resp = httpx.post(url, json=data, headers=self.headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def trigger_webhook(self, webhook_path: str, data: dict[str, Any]) -> dict[str, Any]:
        """Call an N8N webhook endpoint directly."""
        url = f"{self.base_url}/webhook/{webhook_path}"
        resp = httpx.post(url, json=data, headers=self.headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def get_execution_status(self, execution_id: str) -> dict[str, Any]:
        """Poll a workflow execution for its current status."""
        url = f"{self.base_url}/api/v1/executions/{execution_id}"
        resp = httpx.get(url, headers=self.headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
