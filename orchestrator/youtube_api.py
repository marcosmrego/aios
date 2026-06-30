"""YouTube Analytics API — serves metrics and triggers daily sync."""

from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query
from fastapi.responses import FileResponse

router = APIRouter(prefix="/youtube", tags=["youtube"])
metrics_router = APIRouter(prefix="/api/youtube", tags=["youtube-metrics"])
log = logging.getLogger(__name__)

_DSN = os.environ.get(
    "YOUTUBE_POSTGRES_DSN",
    "postgresql://postgres:2qS3CODTaQ42mgOYvgb5FKLp8906qTCb94vg5XQKziszz12O8lC6En2GJsW9qQ0q@212.85.22.227:5432/youtube_analytics",
)

_METRICS_TOKEN = os.environ.get("YOUTUBE_METRICS_TOKEN", "")


def _check_token(authorization: str | None, token: str | None) -> None:
    if not _METRICS_TOKEN:
        return
    provided = token or ""
    if not provided and authorization:
        provided = authorization.removeprefix("Bearer ").strip()
    if provided != _METRICS_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _parse_duration(iso: str | None) -> int:
    """Convert ISO 8601 duration (PT2M41S) to total seconds."""
    if not iso:
        return 0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return 0
    h, mn, s = (int(g or 0) for g in m.groups())
    return h * 3600 + mn * 60 + s


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


# ── External metrics API (/api/youtube/metrics) ───────────────────────────────

@metrics_router.get("/metrics")
def external_metrics(
    days: int = Query(30, ge=1, le=365),
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
):
    """
    Structured YouTube metrics for external consumption (e.g. Claude analysis).
    Auth: Bearer token via Authorization header or ?token= query param.
    """
    _check_token(authorization, token)

    window_start = date.today() - timedelta(days=days)

    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

                # Last sync timestamp
                cur.execute("SELECT MAX(synced_at) AS ts FROM video_metrics")
                last_sync = cur.fetchone()["ts"]

                # All videos with lifetime totals + duration
                cur.execute("""
                    SELECT
                        v.video_id,
                        v.title,
                        LEFT(v.published_at::text, 10)                             AS published_at,
                        v.duration                                                  AS duration_iso,
                        COALESCE(SUM(vm.views), 0)                                 AS total_views,
                        ROUND(COALESCE(AVG(NULLIF(vm.average_view_duration,0)),0)::numeric,1)
                                                                                   AS avg_view_duration,
                        ROUND(COALESCE(AVG(NULLIF(vm.average_view_percentage,0)),0)::numeric,2)
                                                                                   AS avg_view_percentage,
                        COALESCE(SUM(vm.subscribers_gained), 0)                    AS total_subscribers_gained
                    FROM videos v
                    LEFT JOIN video_metrics vm ON vm.video_id = v.video_id
                    GROUP BY v.video_id, v.title, v.published_at, v.duration
                    ORDER BY v.published_at DESC
                """)
                video_rows = cur.fetchall()

                # Daily metrics for the requested window
                cur.execute("""
                    SELECT
                        video_id,
                        date::text,
                        views,
                        ROUND(average_view_duration::numeric, 1)    AS average_view_duration_seconds,
                        ROUND(average_view_percentage::numeric, 2)  AS average_view_percentage,
                        subscribers_gained
                    FROM video_metrics
                    WHERE date >= %s AND date < CURRENT_DATE
                    ORDER BY video_id, date
                """, (window_start,))
                daily_rows = cur.fetchall()

        # Group daily rows by video_id
        daily_by_video: dict[str, list[dict]] = {}
        for r in daily_rows:
            vid = r["video_id"]
            if vid not in daily_by_video:
                daily_by_video[vid] = []
            daily_by_video[vid].append({
                "date":                          r["date"],
                "views":                         int(r["views"]),
                "average_view_duration_seconds": float(r["average_view_duration_seconds"]),
                "average_view_percentage":       float(r["average_view_percentage"]),
                "subscribers_gained":            int(r["subscribers_gained"]),
            })

        videos_out = []
        for v in video_rows:
            videos_out.append({
                "video_id":         v["video_id"],
                "title":            v["title"],
                "published_at":     v["published_at"],
                "duration_seconds": _parse_duration(v["duration_iso"]),
                "metrics_total": {
                    "views":                         int(v["total_views"]),
                    "average_view_duration_seconds": float(v["avg_view_duration"]),
                    "average_view_percentage":       float(v["avg_view_percentage"]),
                    "subscribers_gained":            int(v["total_subscribers_gained"]),
                },
                "metrics_daily": daily_by_video.get(v["video_id"], []),
            })

        updated_at = (
            last_sync.astimezone(timezone.utc).isoformat()
            if last_sync else datetime.now(timezone.utc).isoformat()
        )

        return {
            "updated_at":  updated_at,
            "channel":     "Music Videos",
            "channel_id":  "UCOV_BLSt1sD76XUVOazPXrg",
            "window_days": days,
            "videos":      videos_out,
        }

    except HTTPException:
        raise
    except Exception as exc:
        log.error("external_metrics error: %s", exc)
        raise HTTPException(status_code=500, detail="Erro ao ler métricas")
