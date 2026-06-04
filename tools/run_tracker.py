"""Pipeline run tracking — persists run/stage state for the dashboard."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


_CREATE_STORIES_SQL = """
CREATE TABLE IF NOT EXISTS pipeline_stories (
    id          SERIAL PRIMARY KEY,
    sprint      VARCHAR(20)  NOT NULL,
    story_id    VARCHAR(20)  NOT NULL,
    title       TEXT         DEFAULT '',
    project     VARCHAR(50)  DEFAULT 'expansao',
    epic_id     VARCHAR(50)  DEFAULT '',
    epic_title  TEXT         DEFAULT '',
    prd_title   TEXT         DEFAULT '',
    status      VARCHAR(20)  NOT NULL DEFAULT 'backlog',
    dev_files   INTEGER      DEFAULT 0,
    qa_result   VARCHAR(20)  DEFAULT '',
    qa_notes    TEXT         DEFAULT '',
    updated_at  TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE(sprint, story_id)
);

-- Safe migration for existing tables
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='pipeline_stories' AND column_name='epic_id') THEN
        ALTER TABLE pipeline_stories ADD COLUMN epic_id VARCHAR(50) DEFAULT '';
        ALTER TABLE pipeline_stories ADD COLUMN epic_title TEXT DEFAULT '';
    END IF;
END $$;
"""

_CREATE_CREDITS_SQL = """
CREATE TABLE IF NOT EXISTS credit_topups (
    id          SERIAL PRIMARY KEY,
    amount_usd  NUMERIC(10,4) NOT NULL,
    topup_date  DATE NOT NULL,
    notes       TEXT DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

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


_active_run_id: str = ""


def set_active_run_id(run_id: str) -> None:
    global _active_run_id
    _active_run_id = run_id


def get_active_run_id() -> str:
    return _active_run_id


def _conn():
    from orchestrator.settings import settings  # noqa: PLC0415
    import psycopg2  # noqa: PLC0415
    if not settings.database_url:
        return None
    c = psycopg2.connect(settings.database_url)
    with c.cursor() as cur:
        cur.execute(_CREATE_SQL)
        cur.execute(_CREATE_CREDITS_SQL)
        cur.execute(_CREATE_STORIES_SQL)
    c.commit()
    return c


def new_run_id() -> str:
    return f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def start_run(run_id: str, project: str, pipeline: str, extra_context: str = "") -> None:
    set_active_run_id(run_id)
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
            runs = [dict(r) for r in cur.fetchall()]

            # Attach stages and gates to each run
            run_ids = [r["run_id"] for r in runs]
            if run_ids:
                cur.execute(
                    "SELECT run_id, stage_name, status, cost_usd, input_tokens, output_tokens, output_summary, error_msg "
                    "FROM pipeline_stages WHERE run_id = ANY(%s) ORDER BY id",
                    (run_ids,),
                )
                all_stages = cur.fetchall()
                stages_by_run: dict = {}
                for s in all_stages:
                    stages_by_run.setdefault(s["run_id"], []).append(dict(s))

                cur.execute(
                    "SELECT run_id, gate_id, decision FROM gate_decisions WHERE run_id = ANY(%s)",
                    (run_ids,),
                )
                all_gates = cur.fetchall()
                gates_by_run: dict = {}
                for g in all_gates:
                    gates_by_run.setdefault(g["run_id"], []).append(dict(g))

                for r in runs:
                    r["stages"] = stages_by_run.get(r["run_id"], [])
                    r["gates"]  = gates_by_run.get(r["run_id"], [])

        return runs
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


def upsert_story(sprint: str, story_id: str, title: str = "", project: str = "",
                 epic_id: str = "", epic_title: str = "", prd_title: str = "",
                 status: str = "backlog", dev_files: int = 0,
                 qa_result: str = "", qa_notes: str = "") -> None:
    c = _conn()
    if not c:
        return
    try:
        with c.cursor() as cur:
            # project: INSERT uses fallback 'expansao' when empty;
            # UPDATE only overwrites when a non-empty project is explicitly passed.
            # This ensures the Project→Epic→Story hierarchy is never silently broken
            # by downstream agents (Dev, QA, dashboard) that only know the story_id.
            cur.execute("""
                INSERT INTO pipeline_stories
                    (sprint, story_id, title, project, epic_id, epic_title, prd_title,
                     status, dev_files, qa_result, qa_notes, updated_at)
                VALUES (%s, %s, %s, COALESCE(NULLIF(%s,''),'expansao'), %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (sprint, story_id) DO UPDATE SET
                    title       = CASE WHEN EXCLUDED.title      != '' THEN EXCLUDED.title      ELSE pipeline_stories.title END,
                    project     = CASE WHEN %s                  != '' THEN %s                  ELSE pipeline_stories.project END,
                    epic_id     = CASE WHEN EXCLUDED.epic_id    != '' THEN EXCLUDED.epic_id    ELSE pipeline_stories.epic_id END,
                    epic_title  = CASE WHEN EXCLUDED.epic_title != '' THEN EXCLUDED.epic_title ELSE pipeline_stories.epic_title END,
                    prd_title   = CASE WHEN EXCLUDED.prd_title  != '' THEN EXCLUDED.prd_title  ELSE pipeline_stories.prd_title END,
                    status      = CASE WHEN EXCLUDED.status     != 'backlog' THEN EXCLUDED.status ELSE pipeline_stories.status END,
                    dev_files   = CASE WHEN EXCLUDED.dev_files  != 0   THEN EXCLUDED.dev_files  ELSE pipeline_stories.dev_files END,
                    qa_result   = CASE WHEN EXCLUDED.qa_result  != '' THEN EXCLUDED.qa_result  ELSE pipeline_stories.qa_result END,
                    qa_notes    = CASE WHEN EXCLUDED.qa_notes   != '' THEN EXCLUDED.qa_notes   ELSE pipeline_stories.qa_notes END,
                    updated_at  = NOW()
            """, (sprint, story_id, title, project, epic_id, epic_title, prd_title,
                  status, dev_files, qa_result, qa_notes,
                  project, project))
        c.commit()
        try:
            from orchestrator.dashboard_api import emit_event  # noqa: PLC0415
            emit_event({"type": "story_update", "sprint": sprint, "story_id": story_id, "status": status})
        except Exception:
            pass
    except Exception:
        c.rollback()
    finally:
        c.close()


def get_stories(sprint: str | None = None) -> list[dict]:
    c = _conn()
    if not c:
        return []
    try:
        import psycopg2.extras  # noqa: PLC0415
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if sprint:
                cur.execute("SELECT * FROM pipeline_stories WHERE sprint=%s ORDER BY story_id", (sprint,))
            else:
                cur.execute("SELECT * FROM pipeline_stories ORDER BY sprint DESC, story_id")
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def add_topup(amount_usd: float, topup_date: str, notes: str = "") -> dict | None:
    """Insert a credit top-up record. topup_date is ISO string YYYY-MM-DD."""
    c = _conn()
    if not c:
        return None
    try:
        import psycopg2.extras  # noqa: PLC0415
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO credit_topups (amount_usd, topup_date, notes) VALUES (%s, %s, %s) RETURNING *",
                (amount_usd, topup_date, notes),
            )
            row = dict(cur.fetchone())
        c.commit()
        return row
    except Exception:
        c.rollback()
        return None
    finally:
        c.close()


def delete_topup(topup_id: int) -> bool:
    c = _conn()
    if not c:
        return False
    try:
        with c.cursor() as cur:
            cur.execute("DELETE FROM credit_topups WHERE id = %s", (topup_id,))
        c.commit()
        return True
    except Exception:
        c.rollback()
        return False
    finally:
        c.close()


def get_topups() -> list[dict]:
    c = _conn()
    if not c:
        return []
    try:
        import psycopg2.extras  # noqa: PLC0415
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM credit_topups ORDER BY topup_date DESC, created_at DESC")
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def get_credit_summary() -> dict:
    """Calculate estimated remaining balance from topup history vs pipeline spend."""
    c = _conn()
    if not c:
        return {"configured": False}
    try:
        import psycopg2.extras  # noqa: PLC0415
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Total topped up ever
            cur.execute("SELECT COALESCE(SUM(amount_usd), 0) AS total, MIN(topup_date) AS first_date FROM credit_topups")
            row = dict(cur.fetchone())
            total_topup = float(row["total"])
            first_date = row["first_date"]

            if not first_date:
                return {"configured": False}

            # All topups list
            cur.execute("SELECT * FROM credit_topups ORDER BY topup_date DESC")
            topups = [dict(r) for r in cur.fetchall()]

            # Total spent since the first top-up date (pipeline runs)
            cur.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM pipeline_runs WHERE started_at::date >= %s",
                (first_date,),
            )
            pipeline_spent = float(cur.fetchone()["total"])

            # Also count from agent_runs table (external projects like Climate)
            try:
                cur.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM agent_runs WHERE run_at::date >= %s",
                    (first_date,),
                )
                agent_spent = float(cur.fetchone()["total"])
            except Exception:
                agent_spent = 0.0

        total_spent = pipeline_spent + agent_spent
        estimated = max(0.0, total_topup - total_spent)
        pct = (estimated / total_topup * 100) if total_topup > 0 else 0

        return {
            "configured": True,
            "total_topup": round(total_topup, 4),
            "total_spent": round(total_spent, 4),
            "pipeline_spent": round(pipeline_spent, 4),
            "agent_spent": round(agent_spent, 4),
            "estimated_remaining": round(estimated, 4),
            "percent_remaining": round(pct, 1),
            "since_date": first_date.isoformat() if first_date else None,
            "topups": topups,
        }
    except Exception as e:
        return {"configured": False, "error": str(e)}
    finally:
        c.close()


def get_cost_summary() -> dict:
    c = _conn()
    if not c:
        return {}
    try:
        import psycopg2.extras  # noqa: PLC0415
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # pipeline_runs costs (accumulated via complete_stage)
            cur.execute("""
                SELECT
                    COALESCE(SUM(cost_usd) FILTER (WHERE started_at >= NOW() - INTERVAL '1 day'), 0) AS today,
                    COALESCE(SUM(cost_usd) FILTER (WHERE started_at >= NOW() - INTERVAL '7 days'), 0) AS week,
                    COALESCE(SUM(cost_usd) FILTER (WHERE started_at >= NOW() - INTERVAL '30 days'), 0) AS month,
                    COALESCE(SUM(cost_usd), 0) AS total
                FROM pipeline_runs
            """)
            totals = dict(cur.fetchone())
            # agent_runs costs (per-call tracking — includes runs killed before complete_stage)
            cur.execute("""
                SELECT
                    COALESCE(SUM(cost_usd) FILTER (WHERE run_at >= NOW() - INTERVAL '1 day'), 0) AS today,
                    COALESCE(SUM(cost_usd) FILTER (WHERE run_at >= NOW() - INTERVAL '7 days'), 0) AS week,
                    COALESCE(SUM(cost_usd) FILTER (WHERE run_at >= NOW() - INTERVAL '30 days'), 0) AS month,
                    COALESCE(SUM(cost_usd), 0) AS total
                FROM agent_runs
            """)
            agent_totals = dict(cur.fetchone())
            for key in ("today", "week", "month", "total"):
                totals[key] = float(totals[key]) + float(agent_totals[key])
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
