"""Usage and cost tracking for every agent run — all Expansao AI projects.

Writes to PostgreSQL when DATABASE_URL is set; falls back to JSONL otherwise.

Other projects (Climate, GRC Flow) report usage via POST /track on the AIOS API.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from orchestrator.settings import settings

# Pricing in USD per 1M tokens (input / output)
_COST_PER_1M: dict[str, dict[str, float]] = {
    "claude-opus-4-8":           {"input": 15.00, "output": 75.00},
    "claude-opus-4-6":           {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6":         {"input":  3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input":  0.25, "output":  1.25},
}

_FALLBACK_LOG = Path("outputs/agent_runs.jsonl")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id            SERIAL PRIMARY KEY,
    run_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    project       VARCHAR(100) NOT NULL DEFAULT 'aios',
    pipeline      VARCHAR(50)  NOT NULL DEFAULT '',
    agent_name    VARCHAR(100) NOT NULL,
    model         VARCHAR(100) NOT NULL,
    input_tokens  INTEGER      NOT NULL,
    output_tokens INTEGER      NOT NULL,
    cost_usd      NUMERIC(10,6) NOT NULL,
    duration_ms   INTEGER
)
"""

# Safe migration if table already exists without the project column
_MIGRATE_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'agent_runs' AND column_name = 'project'
    ) THEN
        ALTER TABLE agent_runs ADD COLUMN project VARCHAR(100) NOT NULL DEFAULT 'aios';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'agent_runs' AND column_name = 'pipeline'
    ) THEN
        ALTER TABLE agent_runs ADD COLUMN pipeline VARCHAR(50) NOT NULL DEFAULT '';
    END IF;
END $$;
"""

_INSERT_SQL = """
INSERT INTO agent_runs
    (project, pipeline, agent_name, model, input_tokens, output_tokens, cost_usd, duration_ms)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = _COST_PER_1M.get(model, {"input": 3.00, "output": 15.00})
    return (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000


def _get_connection():  # type: ignore[return]
    import psycopg2  # noqa: PLC0415
    conn = psycopg2.connect(settings.database_url)
    with conn.cursor() as cur:
        cur.execute(_CREATE_TABLE_SQL)
        cur.execute(_MIGRATE_SQL)
    conn.commit()
    return conn


def log_run(
    project: str,
    pipeline: str,
    agent_name: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    duration_ms: int,
) -> None:
    record: dict[str, Any] = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "project": project,
        "pipeline": pipeline,
        "agent_name": agent_name,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd, 6),
        "duration_ms": duration_ms,
    }

    if settings.database_url:
        try:
            conn = _get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    _INSERT_SQL,
                    (project, pipeline, agent_name, model, input_tokens, output_tokens, cost_usd, duration_ms),
                )
            conn.commit()
            conn.close()
            return
        except Exception:
            pass  # fall through to JSONL fallback

    _FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _FALLBACK_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def query_summary(days: int = 30) -> dict[str, Any]:
    """Aggregate cost and usage by project and agent for the past N days."""
    if settings.database_url:
        return _summary_from_db(days)
    return _summary_from_jsonl(days)


def _summary_from_db(days: int) -> dict[str, Any]:
    try:
        import psycopg2.extras  # noqa: PLC0415

        conn = _get_connection()
        since = datetime.now(timezone.utc) - timedelta(days=days)

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS runs,
                       COALESCE(SUM(cost_usd), 0)      AS total_cost_usd,
                       COALESCE(SUM(input_tokens), 0)  AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens
                FROM agent_runs WHERE run_at > %s
                """,
                (since,),
            )
            total = dict(cur.fetchone())  # type: ignore[arg-type]

            cur.execute(
                """
                SELECT project,
                       COUNT(*)                        AS runs,
                       COALESCE(SUM(cost_usd), 0)      AS cost_usd,
                       COALESCE(SUM(input_tokens), 0)  AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens
                FROM agent_runs WHERE run_at > %s
                GROUP BY project ORDER BY cost_usd DESC
                """,
                (since,),
            )
            by_project = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT project, agent_name, model,
                       COUNT(*)                       AS runs,
                       COALESCE(SUM(cost_usd), 0)     AS cost_usd,
                       COALESCE(AVG(duration_ms), 0)  AS avg_duration_ms
                FROM agent_runs WHERE run_at > %s
                GROUP BY project, agent_name, model ORDER BY cost_usd DESC
                """,
                (since,),
            )
            by_agent = [dict(r) for r in cur.fetchall()]

        conn.close()
        return {
            "period_days": days,
            "source": "postgresql",
            "total": {k: float(v) if k != "runs" else int(v) for k, v in total.items()},
            "by_project": [
                {k: float(v) if k not in ("project", "runs") else (int(v) if k == "runs" else v) for k, v in r.items()}
                for r in by_project
            ],
            "by_agent": [
                {k: float(v) if k not in ("project", "agent_name", "model", "runs") else (int(v) if k == "runs" else v) for k, v in r.items()}
                for r in by_agent
            ],
        }
    except Exception as e:
        return {"error": str(e), "period_days": days}


def _summary_from_jsonl(days: int) -> dict[str, Any]:
    if not _FALLBACK_LOG.exists():
        return {"period_days": days, "source": "jsonl", "total": {}, "by_project": [], "by_agent": []}

    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = []
    with _FALLBACK_LOG.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                if datetime.fromisoformat(r["run_at"]) > since:
                    rows.append(r)
            except Exception:
                continue

    total_cost = sum(r["cost_usd"] for r in rows)
    total_input = sum(r["input_tokens"] for r in rows)
    total_output = sum(r["output_tokens"] for r in rows)

    projects: dict[str, Any] = {}
    agents: dict[str, Any] = {}
    for r in rows:
        p = r.get("project", "aios")
        projects.setdefault(p, {"runs": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0})
        projects[p]["runs"] += 1
        projects[p]["cost_usd"] += r["cost_usd"]
        projects[p]["input_tokens"] += r["input_tokens"]
        projects[p]["output_tokens"] += r["output_tokens"]

        key = f"{p}|{r['agent_name']}|{r['model']}"
        agents.setdefault(key, {"project": p, "agent_name": r["agent_name"], "model": r["model"], "runs": 0, "cost_usd": 0.0})
        agents[key]["runs"] += 1
        agents[key]["cost_usd"] += r["cost_usd"]

    return {
        "period_days": days,
        "source": "jsonl",
        "total": {"runs": len(rows), "total_cost_usd": total_cost, "input_tokens": total_input, "output_tokens": total_output},
        "by_project": sorted(projects.items(), key=lambda x: x[1]["cost_usd"], reverse=True),
        "by_agent": sorted(agents.values(), key=lambda x: x["cost_usd"], reverse=True),
    }
