"""Slack webhook notifications."""

from __future__ import annotations

import httpx

from orchestrator.settings import settings


def post_slack_message(text: str) -> bool:
    """Post a message to the configured Slack webhook. Returns True on success."""
    if not settings.slack_webhook_url:
        return False
    try:
        resp = httpx.post(settings.slack_webhook_url, json={"text": text}, timeout=10)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False
