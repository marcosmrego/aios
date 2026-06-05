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

    def get_deployment_status(self, deployment_uuid: str) -> dict[str, Any]:
        resp = httpx.get(
            f"{self.base_url}/api/v1/deployments/{deployment_uuid}",
            headers=self.headers,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def get_deployment_logs(self, deployment_uuid: str) -> str:
        """Fetch deployment logs. Returns empty string on failure."""
        try:
            resp = httpx.get(
                f"{self.base_url}/api/v1/deployments/{deployment_uuid}/logs",
                headers=self.headers,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return "\n".join(
                    line.get("output", str(line)) if isinstance(line, dict) else str(line)
                    for line in data
                )
            if isinstance(data, dict):
                return data.get("logs", str(data))
            return str(data)
        except Exception:
            return ""

    def wait_for_deploy(self, deployment_uuid: str, timeout: int = 300,
                        poll_interval: int = 15) -> str:
        """Poll until deploy finishes. Returns: 'finished', 'failed', 'cancelled', or 'timeout'."""
        import time  # noqa: PLC0415
        terminal = {"finished", "failed", "cancelled", "error"}
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data = self.get_deployment_status(deployment_uuid)
                status = data.get("status", "")
                if status in terminal:
                    return status
            except Exception:
                pass
            time.sleep(poll_interval)
        return "timeout"

    def trigger_deploy(self, app_uuid: str) -> dict[str, Any]:
        """Trigger a deploy and return the deployment info."""
        return self.deploy_application(app_uuid)

    def get_env_vars(self, app_uuid: str) -> list[dict[str, Any]]:
        """Return all env vars for an application."""
        resp = httpx.get(
            f"{self.base_url}/api/v1/applications/{app_uuid}/envs",
            headers=self.headers,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def set_env_var(self, app_uuid: str, key: str, value: str) -> dict[str, Any]:
        """Create or update an env var. Handles 409 (already exists) by PATCHing."""
        payload = {"key": key, "value": value, "is_buildtime": True, "is_runtime": True}
        resp = httpx.post(
            f"{self.base_url}/api/v1/applications/{app_uuid}/envs",
            headers=self.headers,
            json=payload,
            timeout=_TIMEOUT,
        )
        if resp.status_code == 409:
            resp = httpx.patch(
                f"{self.base_url}/api/v1/applications/{app_uuid}/envs",
                headers=self.headers,
                json=payload,
                timeout=_TIMEOUT,
            )
        resp.raise_for_status()
        return {"key": key, "status": resp.status_code}

    def set_env_vars(self, app_uuid: str, env_vars: dict[str, str]) -> list[dict[str, Any]]:
        """Create or update multiple env vars at once."""
        return [self.set_env_var(app_uuid, k, v) for k, v in env_vars.items()]

    def redeploy(self, app_uuid: str) -> dict[str, Any]:
        """Trigger redeploy and return deployment UUID."""
        resp = httpx.post(
            f"{self.base_url}/api/v1/deploy?uuid={app_uuid}&force=false",
            headers=self.headers,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "app_uuid": app_uuid,
            "deployment_uuid": data.get("deployments", [{}])[0].get("deployment_uuid", ""),
        }
