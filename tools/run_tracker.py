"""Pipeline run tracking — persists run/stage state for the dashboard."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id            SERIAL PRIMARY KEY,
    run_id        VARCHAR(50)  UNIQUE NOT NULL,
    project       VARCHAR(50)  NOT NULL,
    pipeline      VARCHAR(50)  NOT NULL,
    status        VARCHAR(20)  NOT NULL DEFAULT 'running',
    current_stage VARCHAR(50),
    extra_context TEXT         DEFAULT '',
    cost_usd      NUMERIC(10,6) DEFAULT 0,
    started_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at  TIMESTAMPTZ,
    error_msg     TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_stages (
    id            SERIAL PRIMARY KEY,
    run_id        VARCHAR(50)  NOT NULL,
    stage_name    VARCHAR(50)  NOT NULL,
    status        VARCHAR(20)  NOT NULL DEFAULT 'pending',
    cost_usd      NUMERIC(10,6) DEFAULT 0,
    input_tokens  INTEGER      DEFAULT 0,
    output_tokens INTEGER      DEFAULT 0,
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ,
    error_msg     TEXT,
    output_summary TEXT,
    UNIQUE(run_id, stage_name)
);

CREATE TABLE IF NOT EXISTS gate_decisions (
    id          SERIAL PRIMARY KEY,
    run_id      VARCHAR(50) NOT NULL,
    gate_id     VARCHAR(50) NOT NULL,
    decision    VARCHAR(20) DEFAULT 'pending',
    decided_at  TIMESTAMPTZ,
    UNIQUE(run_id, gate_id)
);
"""


def _conn():
    from orchestrator.settings import settings  # noqa: PLC0415
    import psycopg2  # noqa: PLC0415
    if not settings.database_url:
        return None
    c = psycopg2.connect(settings.database_url)
    with c.cursor() as cur:
        cur.execute(_CREATE_SQL)
    c.commit()
    return c


def new_run_id() -> str:
    return f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def start_run(run_id: str, project: str, pipeline: str, extra_context: str = "") -> None:
    c = _conn()
    if not c:
        return
    try:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO pipeline_runs (run_id, project, pipeline, extra_context) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (run_id) DO NOTHING",
                (run_id, project, pipeline, extra_context[:500]),
            )
        c.commit()
    except Exception:
        pass
    finally:
        c.close()


def update_run(run_id: str, status: str, current_stage: str | None = None,
               cost_usd: float | None = None, error_msg: str | None = None) -> None:
    c = _conn()
    if not c:
        return
    try:
        fields = ["status = %s"]
        values: list[Any] = [status]
        if current_stage is not None:
            fields.append("current_stage = %s")
            values.append(current_stage)
        if cost_usd is not None:
            fields.append("cost_usd = %s")
            values.append(cost_usd)
        if error_msg is not None:
            fields.append("error_msg = %s")
            values.append(error_msg[:1000])
        if status in ("completed", "failed"):
            fields.append("completed_at = NOW()")
        values.append(run_id)
        with c.cursor() as cur:
            cur.execute(f"UPDATE pipeline_runs SET {', '.join(fields)} WHERE run_id = %s", values)
        c.commit()
    except Exception:
        pass
    finally:
        c.close()


def start_stage(run_id: str, stage_name: str) -> None:
    c = _conn()
    if not c:
        return
    try:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO pipeline_stages (run_id, stage_name, status, started_at) "
                "VALUES (%s, %s, 'running', NOW()) "
                "ON CONFLICT (run_id, stage_name) DO UPDATE SET status='running', started_at=NOW()",
                (run_id, stage_name),
            )
            cur.execute(
                "UPDATE pipeline_runs SET current_stage = %s WHERE run_id = %s",
                (stage_name, run_id),
            )
        c.commit()
    except Exception:
        pass
    finally:
        c.close()


def complete_stage(run_id: str, stage_name: str, status: str = "completed",
                   cost_usd: float = 0, input_tokens: int = 0, output_tokens: int = 0,
                   output_summary: str = "", error_msg: str = "") -> None:
    c = _conn()
    if not c:
        return
    try:
        with c.cursor() as cur:
            cur.execute(
                """UPDATE pipeline_stages SET
                    status = %s, cost_usd = %s, input_tokens = %s, output_tokens = %s,
                    completed_at = NOW(), output_summary = %s, error_msg = %s
                   WHERE run_id = %s AND stage_name = %s""",
                (status, cost_usd, input_tokens, output_tokens,
                 output_summary[:500], error_msg[:500], run_id, stage_name),
            )
            cur.execute(
                "UPDATE pipeline_runs SET cost_usd = cost_usd + %s WHERE run_id = %s",
                (cost_usd, run_id),
            )
        c.commit()
    except Exception:
        pass
    finally:
        c.close()


def skip_stage(run_id: str, stage_name: str, reason: str = "cached") -> None:
    c = _conn()
    if not c:
        return
    try:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO pipeline_stages (run_id, stage_name, status, output_summary) "
                "VALUES (%s, %s, 'skipped', %s) "
                "ON CONFLICT (run_id, stage_name) DO UPDATE SET status='skipped', output_summary=%s",
                (run_id, stage_name, reason, reason),
            )
        c.commit()
    except Exception:
        pass
    finally:
        c.close()


def set_gate(run_id: str, gate_id: str, decision: str = "pending") -> None:
    c = _conn()
    if not c:
        return
    try:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO gate_decisions (run_id, gate_id, decision, decided_at) "
                "VALUES (%s, %s, %s, CASE WHEN %s != 'pending' THEN NOW() ELSE NULL END) "
                "ON CONFLICT (run_id, gate_id) DO UPDATE SET decision=%s, "
                "decided_at=CASE WHEN %s != 'pending' THEN NOW() ELSE gate_decisions.decided_at END",
                (run_id, gate_id, decision, decision, decision, decision),
            )
        c.commit()
    except Exception:
        pass
    finally:
        c.close()


def get_gate_decision(run_id: str, gate_id: str) -> str:
    """Returns 'approved', 'rejected', or 'pending'."""
    c = _conn()
    if not c:
        return "approved"  # no DB = auto-approve
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT decision FROM gate_decisions WHERE run_id=%s AND gate_id=%s",
                (run_id, gate_id),
            )
            row = cur.fetchone()
        return row[0] if row else "pending"
    except Exception:
        return "approved"
    finally:
        c.close()


def get_runs(limit: int = 50) -> list[dict]:
    c = _conn()
    if not c:
        return []
    try:
        import psycopg2.extras  # noqa: PLC0415
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT %s", (limit,)
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def get_run_detail(run_id: str) -> dict | None:
    c = _conn()
    if not c:
        return None
    try:
        import psycopg2.extras  # noqa: PLC0415
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM pipeline_runs WHERE run_id=%s", (run_id,))
            run = cur.fetchone()
            if not run:
                return None
            run = dict(run)
            cur.execute(
                "SELECT * FROM pipeline_stages WHERE run_id=%s ORDER BY id", (run_id,)
            )
            run["stages"] = [dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT * FROM gate_decisions WHERE run_id=%s", (run_id,)
            )
            run["gates"] = [dict(r) for r in cur.fetchall()]
        return run
    except Exception:
        return None
    finally:
        c.close()


def get_cost_summary() -> dict:
    c = _conn()
    if not c:
        return {}
    try:
        import psycopg2.extras  # noqa: PLC0415
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    COALESCE(SUM(cost_usd) FILTER (WHERE started_at >= NOW() - INTERVAL '1 day'), 0) AS today,
                    COALESCE(SUM(cost_usd) FILTER (WHERE started_at >= NOW() - INTERVAL '7 days'), 0) AS week,
                    COALESCE(SUM(cost_usd) FILTER (WHERE started_at >= NOW() - INTERVAL '30 days'), 0) AS month,
                    COALESCE(SUM(cost_usd), 0) AS total
                FROM pipeline_runs
            """)
            totals = dict(cur.fetchone())
            cur.execute("""
                SELECT project, pipeline,
                       COUNT(*) AS runs,
                       COALESCE(SUM(cost_usd), 0) AS cost_usd
                FROM pipeline_runs
                WHERE started_at >= NOW() - INTERVAL '30 days'
                GROUP BY project, pipeline ORDER BY cost_usd DESC
            """)
            by_project = [dict(r) for r in cur.fetchall()]
        return {"totals": {k: float(v) for k, v in totals.items()}, "by_project": by_project}
    except Exception:
        return {}
    finally:
        c.close()
