"""YouTube Analytics API — serves metrics and triggers daily sync."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import date, timedelta

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query
from fastapi.responses import FileResponse

router = APIRouter(prefix="/youtube", tags=["youtube"])
log = logging.getLogger(__name__)

_DSN = os.environ.get(
    "YOUTUBE_POSTGRES_DSN",
    "postgresql://postgres:2qS3CODTaQ42mgOYvgb5FKLp8906qTCb94vg5XQKziszz12O8lC6En2GJsW9qQ0q@212.85.22.227:5432/youtube_analytics",
)


@contextmanager
def _conn():
    conn = psycopg2.connect(_DSN)
    try:
        yield conn
    finally:
        conn.close()


@router.get("/")
def youtube_page():
    return FileResponse("static/youtube/index.html")


@router.get("/api/kpis")
def kpis(days: int = Query(30, ge=1, le=365)):
    start = date.today() - timedelta(days=days)
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        COALESCE(SUM(views), 0)                                       AS total_views,
                        COALESCE(SUM(estimated_minutes_watched), 0)                   AS total_minutes,
                        COALESCE(SUM(subscribers_gained), 0)                          AS total_subs,
                        COALESCE(AVG(NULLIF(average_view_duration, 0)), 0)            AS avg_duration,
                        COALESCE(AVG(NULLIF(average_view_percentage, 0)), 0)          AS avg_pct,
                        MAX(synced_at)                                                AS last_synced
                    FROM video_metrics
                    WHERE date >= %s AND date < CURRENT_DATE
                    """,
                    (start,),
                )
                row = cur.fetchone()
                cur.execute("SELECT COUNT(*) AS cnt FROM videos")
                video_count = cur.fetchone()["cnt"]

        return {
            "period": {"start": start.isoformat(), "end": (date.today() - timedelta(days=1)).isoformat()},
            "total_views": int(row["total_views"]),
            "total_hours": round(float(row["total_minutes"]) / 60, 1),
            "total_subs": int(row["total_subs"]),
            "avg_duration_s": round(float(row["avg_duration"]), 1),
            "avg_pct": round(float(row["avg_pct"]), 1),
            "video_count": video_count,
            "last_synced": row["last_synced"].isoformat() if row["last_synced"] else None,
        }
    except Exception as exc:
        log.error("kpis error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/timeseries")
def timeseries(days: int = Query(30, ge=1, le=365)):
    start = date.today() - timedelta(days=days)
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        date::text,
                        SUM(views)                                           AS views,
                        ROUND(SUM(estimated_minutes_watched)::numeric/60,2) AS hours_watched,
                        SUM(subscribers_gained)                              AS subs_gained
                    FROM video_metrics
                    WHERE date >= %s AND date < CURRENT_DATE
                    GROUP BY date
                    ORDER BY date
                    """,
                    (start,),
                )
                totals = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    """
                    SELECT
                        vm.date::text,
                        vm.video_id,
                        v.title,
                        vm.views,
                        ROUND(vm.estimated_minutes_watched::numeric/60,2) AS hours_watched
                    FROM video_metrics vm
                    JOIN videos v ON v.video_id = vm.video_id
                    WHERE vm.date >= %s AND vm.date < CURRENT_DATE
                    ORDER BY vm.date, vm.views DESC
                    """,
                    (start,),
                )
                per_video = [dict(r) for r in cur.fetchall()]

        return {"totals": totals, "per_video": per_video}
    except Exception as exc:
        log.error("timeseries error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sync")
def youtube_sync(
    background_tasks: BackgroundTasks,
    x_aios_key: str | None = Header(default=None),
):
    """Trigger YouTube Analytics sync. Called daily by n8n."""
    from orchestrator.settings import settings  # noqa: PLC0415

    if settings.track_api_key and x_aios_key != settings.track_api_key:
        raise HTTPException(status_code=403, detail="Invalid or missing X-AIOS-Key")

    background_tasks.add_task(_do_sync)
    return {"triggered": True, "message": "Sync iniciado em background"}


def _do_sync() -> None:
    from orchestrator.youtube_sync.sync import run_sync  # noqa: PLC0415

    try:
        result = run_sync(days_back=2)
        log.info(
            "YouTube sync OK | videos=%d metrics=%d period=%s->%s",
            result.videos, result.metrics_rows, result.period_start, result.period_end,
        )
    except Exception as exc:
        log.error("YouTube sync falhou: %s", exc, exc_info=True)


@router.get("/api/videos")
def videos(days: int = Query(30, ge=1, le=365)):
    start = date.today() - timedelta(days=days)
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        v.video_id,
                        v.title,
                        LEFT(v.published_at::text, 10)                              AS published_at,
                        COALESCE(SUM(vm.views), 0)                                  AS total_views,
                        ROUND(COALESCE(SUM(vm.estimated_minutes_watched),0)::numeric/60,1)
                                                                                    AS total_hours,
                        ROUND(COALESCE(AVG(NULLIF(vm.average_view_duration,0)),0)::numeric,1)
                                                                                    AS avg_duration_s,
                        ROUND(COALESCE(AVG(NULLIF(vm.average_view_percentage,0)),0)::numeric,1)
                                                                                    AS avg_pct,
                        COALESCE(SUM(vm.subscribers_gained), 0)                     AS total_subs
                    FROM videos v
                    LEFT JOIN video_metrics vm
                           ON vm.video_id = v.video_id
                          AND vm.date >= %s AND vm.date < CURRENT_DATE
                    GROUP BY v.video_id, v.title, v.published_at
                    ORDER BY total_views DESC
                    """,
                    (start,),
                )
                rows = [dict(r) for r in cur.fetchall()]

        return {"videos": rows}
    except Exception as exc:
        log.error("videos error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
