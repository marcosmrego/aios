"""YouTube Analytics daily sync — runs from AIOS and is triggered via /youtube/sync."""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta

import psycopg2
import psycopg2.extras
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

CHANNEL_ID = "UCOV_BLSt1sD76XUVOazPXrg"
OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
_METRICS = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained"
_RETRY_CODES = {429, 500, 503}


@dataclass
class SyncResult:
    videos: int
    metrics_rows: int
    period_start: str
    period_end: str


# ── Auth ──────────────────────────────────────────────────────────────────────

def _build_credentials() -> Credentials:
    client_id     = os.environ["GOOGLE_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_CLIENT_SECRET"]
    refresh_token = os.environ["GOOGLE_REFRESH_TOKEN"]

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=OAUTH_SCOPES,
    )
    creds.refresh(Request())
    return creds


# ── Fetch video list ──────────────────────────────────────────────────────────

def _fetch_videos(creds: Credentials) -> list[dict]:
    yt = build("youtube", "v3", credentials=creds)

    ch = yt.channels().list(part="contentDetails", id=CHANNEL_ID).execute()
    if not ch.get("items"):
        raise RuntimeError(f"Canal {CHANNEL_ID} não encontrado")
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    video_ids: list[str] = []
    page_token = None
    while True:
        resp = yt.playlistItems().list(
            part="contentDetails", playlistId=uploads, maxResults=50, pageToken=page_token
        ).execute()
        for item in resp.get("items", []):
            video_ids.append(item["contentDetails"]["videoId"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    videos: list[dict] = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        vresp = yt.videos().list(part="snippet,contentDetails", id=",".join(batch)).execute()
        for item in vresp.get("items", []):
            videos.append({
                "video_id":     item["id"],
                "title":        item["snippet"]["title"],
                "published_at": item["snippet"]["publishedAt"],
                "duration":     item["contentDetails"]["duration"],
            })

    log.info("%d vídeos encontrados no canal", len(videos))
    return videos


# ── Fetch analytics ───────────────────────────────────────────────────────────

def _fetch_analytics(creds: Credentials, video_ids: list[str], start: date, end: date) -> list[dict]:
    analytics = build("youtubeAnalytics", "v2", credentials=creds)
    all_rows: list[dict] = []

    for video_id in video_ids:
        rows = _query_video(analytics, video_id, start.isoformat(), end.isoformat())
        for r in rows:
            all_rows.append({**r, "video_id": video_id})

    log.info("%d linhas de métricas obtidas", len(all_rows))
    return all_rows


def _query_video(analytics, video_id: str, start: str, end: str) -> list[dict]:
    for attempt in range(1, 4):
        try:
            resp = (
                analytics.reports()
                .query(
                    ids="channel==MINE",
                    startDate=start,
                    endDate=end,
                    dimensions="day",
                    metrics=_METRICS,
                    filters=f"video=={video_id}",
                )
                .execute()
            )
            break
        except HttpError as e:
            if e.resp.status in _RETRY_CODES and attempt < 3:
                time.sleep(2 ** attempt)
            else:
                raise

    if not resp.get("rows"):
        return []

    headers = [h["name"] for h in resp["columnHeaders"]]
    rows = []
    for row in resp["rows"]:
        r = dict(zip(headers, row))
        rows.append({
            "date":                      r["day"],
            "views":                     int(r.get("views") or 0),
            "estimated_minutes_watched": float(r.get("estimatedMinutesWatched") or 0),
            "average_view_duration":     float(r.get("averageViewDuration") or 0),
            "average_view_percentage":   float(r.get("averageViewPercentage") or 0),
            "subscribers_gained":        int(r.get("subscribersGained") or 0),
            "impressions":               None,
            "impression_ctr":            None,
        })
    return rows


# ── Storage ───────────────────────────────────────────────────────────────────

@contextmanager
def _conn():
    dsn = os.environ["YOUTUBE_POSTGRES_DSN"]
    conn = psycopg2.connect(dsn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _upsert_videos(conn, videos: list[dict]) -> None:
    psycopg2.extras.execute_values(
        conn.cursor(),
        """
        INSERT INTO videos (video_id, title, published_at, duration)
        VALUES %s
        ON CONFLICT (video_id) DO UPDATE SET
            title    = EXCLUDED.title,
            duration = EXCLUDED.duration
        """,
        [(v["video_id"], v["title"], v["published_at"], v["duration"]) for v in videos],
    )


def _upsert_metrics(conn, metrics: list[dict]) -> None:
    psycopg2.extras.execute_values(
        conn.cursor(),
        """
        INSERT INTO video_metrics (
            video_id, date, views, impressions, impression_ctr,
            average_view_duration, average_view_percentage,
            subscribers_gained, estimated_minutes_watched
        ) VALUES %s
        ON CONFLICT (video_id, date) DO UPDATE SET
            views                     = EXCLUDED.views,
            average_view_duration     = EXCLUDED.average_view_duration,
            average_view_percentage   = EXCLUDED.average_view_percentage,
            subscribers_gained        = EXCLUDED.subscribers_gained,
            estimated_minutes_watched = EXCLUDED.estimated_minutes_watched,
            synced_at                 = NOW()
        """,
        [
            (
                m["video_id"], m["date"], m["views"], m["impressions"], m["impression_ctr"],
                m["average_view_duration"], m["average_view_percentage"],
                m["subscribers_gained"], m["estimated_minutes_watched"],
            )
            for m in metrics
        ],
    )


# ── Public entry point ────────────────────────────────────────────────────────

def run_sync(days_back: int = 2) -> SyncResult:
    """Fetch and persist YouTube metrics. days_back=2 captures stable D-2 data."""
    end_date   = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=days_back - 1)

    log.info("Sync | canal=%s | %s -> %s", CHANNEL_ID, start_date, end_date)

    creds   = _build_credentials()
    videos  = _fetch_videos(creds)
    ids     = [v["video_id"] for v in videos]
    metrics = _fetch_analytics(creds, ids, start_date, end_date)

    with _conn() as conn:
        _upsert_videos(conn, videos)
        _upsert_metrics(conn, metrics)

    log.info("Sync concluído | %d vídeos | %d linhas", len(videos), len(metrics))
    return SyncResult(
        videos=len(videos),
        metrics_rows=len(metrics),
        period_start=start_date.isoformat(),
        period_end=end_date.isoformat(),
    )
