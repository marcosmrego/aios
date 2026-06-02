"""Slack webhook notifications."""

from __future__ import annotations

import httpx

from orchestrator.settings import settings

_CHANNEL_WEBHOOKS = {
    "cwi": lambda: settings.slack_webhook_url_cwi,
    "expansao": lambda: settings.slack_webhook_url_expansao,
}


def post_slack_message(text: str, channel: str = "expansao") -> bool:
    """Post a message to a Slack channel webhook. Returns True on success.

    channel: "cwi" → #cwi-aios, "expansao" → #expansao-aios (default)
    """
    getter = _CHANNEL_WEBHOOKS.get(channel, lambda: settings.slack_webhook_url_expansao)
    url = getter()
    if not url:
        return False
    try:
        resp = httpx.post(url, json={"text": text}, timeout=10)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False
