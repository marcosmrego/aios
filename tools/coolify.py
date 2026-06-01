"""Coolify API integration — deploy status and health checks."""

from __future__ import annotations

from typing import Any

import httpx

from orchestrator.settings import settings

_TIMEOUT = 30


class CoolifyClient:
    """Thin wrapper for Coolify REST API."""

    def __init__(self) -> None:
        if not hasattr(settings, "coolify_base_url") or not settings.coolify_base_url:  # type: ignore[attr-defined]
            raise RuntimeError("COOLIFY_BASE_URL not configured")
        self.base_url = settings.coolify_base_url.rstrip("/")  # type: ignore[attr-defined]
        self.headers = {
            "Authorization": f"Bearer {settings.coolify_api_key}",  # type: ignore[attr-defined]
            "Content-Type": "application/json",
        }

    def list_applications(self) -> list[dict[str, Any]]:
        resp = httpx.get(f"{self.base_url}/api/v1/applications", headers=self.headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def deploy_application(self, app_uuid: str) -> dict[str, Any]:
        resp = httpx.post(
            f"{self.base_url}/api/v1/applications/{app_uuid}/deploy",
            headers=self.headers,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def get_application_status(self, app_uuid: str) -> dict[str, Any]:
        resp = httpx.get(
            f"{self.base_url}/api/v1/applications/{app_uuid}",
            headers=self.headers,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def health_check(self, url: str, retries: int = 3, timeout: int = 60) -> bool:
        """Verify a deployed service responds with 2xx."""
        import time  # noqa: PLC0415
        for attempt in range(retries):
            try:
                resp = httpx.get(url, timeout=timeout, follow_redirects=True)
                if resp.is_success:
                    return True
            except httpx.HTTPError:
                pass
            if attempt < retries - 1:
                time.sleep(10)
        return False

    def health_check_by_id(self, app_uuid: str) -> bool:
        """Check application status via Coolify API."""
        try:
            data = self.get_application_status(app_uuid)
            return data.get("status") in ("running", "healthy")
        except Exception:
            return False

    def trigger_deploy(self, app_uuid: str) -> dict[str, Any]:
        """Trigger a deploy and return the deployment info."""
        return self.deploy_application(app_uuid)
