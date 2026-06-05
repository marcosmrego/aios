"""Project registry — single source of truth for all Expansão AI projects."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any


_CONFIG_PATH = Path(__file__).parent.parent / "config" / "projects.yaml"


@lru_cache(maxsize=1)
def _load() -> list[dict[str, Any]]:
    import yaml  # noqa: PLC0415
    data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    return data.get("projects", [])


def get_projects() -> list[dict[str, Any]]:
    return _load()


def get_project(slug: str) -> dict[str, Any] | None:
    return next((p for p in _load() if p["slug"] == slug), None)


def get_slugs() -> list[str]:
    return [p["slug"] for p in _load()]


def get_coolify_uuids(slug: str) -> list[str]:
    p = get_project(slug)
    if not p:
        return []
    raw = p.get("coolify_uuid", "") or ""
    return [u.strip() for u in raw.split(",") if u.strip()]


def get_notion_name(slug: str) -> str:
    p = get_project(slug)
    return p.get("notion_name", "") if p else ""


def projects_context_for_prompt() -> str:
    """Returns a markdown block listing all projects — injected into CEO/PM prompts."""
    lines = []
    for p in _load():
        uuid_info = f" | Coolify: `{p['coolify_uuid']}`" if p.get("coolify_uuid") else ""
        lines.append(f"- **{p['slug']}** ({p['name']}): {p['description']}{uuid_info}")
    return "\n".join(lines)


def slug_enum() -> str:
    """Returns pipe-separated slug list for prompt enums, e.g. 'climate|aios|site'."""
    return "|".join(get_slugs())
