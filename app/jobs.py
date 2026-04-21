"""RQ-callable job functions. Kept small so they serialize cleanly."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.models import Group, Post
from app.scraper.posts import refresh_metrics, scrape_group, scrape_groups_batch


log = logging.getLogger(__name__)


def job_scrape_group(group_id: int, full: bool = False) -> dict:
    log.info("scrape_group id=%s full=%s", group_id, full)
    return scrape_group(group_id, full=full)


def job_scrape_all_incremental() -> dict:
    """Scheduled daily: fetch the newest page for every active group via batched execute."""
    with session_scope() as s:
        ids = [r[0] for r in s.execute(select(Group.id).where(Group.active.is_(True))).all()]
    if not ids:
        return {"groups": 0}
    results = scrape_groups_batch(ids)
    total_written = sum(r.get("written", 0) for r in results.values())
    return {"groups": len(ids), "written": total_written}


def job_refresh_metrics_fresh() -> dict:
    """Daily: refresh metrics for posts in the last `fresh_window_days` window."""
    cfg = get_settings()
    since = datetime.now(timezone.utc) - timedelta(days=cfg.fresh_window_days)
    with session_scope() as s:
        ids = [r[0] for r in s.execute(select(Post.id).where(Post.date >= since)).all()]
    return {"count": refresh_metrics(ids)}


def job_refresh_metrics_old(batch: int = 5000) -> dict:
    """Weekly: refresh metrics for posts older than the fresh window.

    Caps at `batch` to keep one weekly run bounded; scheduler can re-enqueue.
    """
    cfg = get_settings()
    until = datetime.now(timezone.utc) - timedelta(days=cfg.fresh_window_days)
    with session_scope() as s:
        ids = [
            r[0]
            for r in s.execute(
                select(Post.id).where(Post.date < until).order_by(Post.updated_at.asc()).limit(batch)
            ).all()
        ]
    return {"count": refresh_metrics(ids)}
