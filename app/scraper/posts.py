"""Crawl a VK community's wall and upsert posts + metrics history."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.db import session_scope
from app.models import Group, Post, PostMetricsHistory
from app.vk.client import VKClient


log = logging.getLogger(__name__)


def _hash_text(text: str) -> str:
    norm = " ".join((text or "").lower().split())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def _post_url(owner_id: int, post_id: int) -> str:
    return f"https://vk.com/wall{owner_id}_{post_id}"


def _to_dt(ts: int | None) -> datetime:
    return datetime.fromtimestamp(int(ts or 0), tz=timezone.utc)


def _extract_metrics(item: dict[str, Any]) -> tuple[int, int, int, int]:
    views = int((item.get("views") or {}).get("count") or 0)
    likes = int((item.get("likes") or {}).get("count") or 0)
    reposts = int((item.get("reposts") or {}).get("count") or 0)
    comments = int((item.get("comments") or {}).get("count") or 0)
    return views, likes, reposts, comments


def _upsert_posts(session, group: Group, items: Iterable[dict[str, Any]]) -> int:
    written = 0
    for item in items:
        vk_post_id = int(item.get("id") or 0)
        if not vk_post_id:
            continue
        owner_id = int(item.get("owner_id") or (-group.vk_id))
        text = item.get("text") or ""
        date = _to_dt(item.get("date"))
        views, likes, reposts, comments = _extract_metrics(item)

        stmt = pg_insert(Post).values(
            group_id=group.id,
            vk_post_id=vk_post_id,
            owner_id=owner_id,
            text=text,
            text_hash=_hash_text(text),
            date=date,
            views=views,
            likes=likes,
            reposts=reposts,
            comments=comments,
            url=_post_url(owner_id, vk_post_id),
            raw=item,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["owner_id", "vk_post_id"],
            set_={
                "text": stmt.excluded.text,
                "text_hash": stmt.excluded.text_hash,
                "views": stmt.excluded.views,
                "likes": stmt.excluded.likes,
                "reposts": stmt.excluded.reposts,
                "comments": stmt.excluded.comments,
                "raw": stmt.excluded.raw,
                "updated_at": datetime.now(timezone.utc),
            },
        ).returning(Post.id)
        post_id = session.execute(stmt).scalar_one()
        session.execute(
            pg_insert(PostMetricsHistory).values(
                post_id=post_id,
                views=views,
                likes=likes,
                reposts=reposts,
                comments=comments,
            )
        )
        written += 1
    return written


def scrape_group(group_id: int, *, full: bool = False, max_pages: int | None = None) -> dict[str, int]:
    """Scrape one group.

    If `full` or the group has never been scraped, paginate the whole wall.
    Otherwise, stop once we reach the previously-known `last_post_id`.
    """
    s_cfg = get_settings()
    page_size = s_cfg.scrape_page_size
    stats = {"fetched": 0, "written": 0, "pages": 0}

    with session_scope() as s:
        group = s.get(Group, group_id)
        if not group or not group.active:
            return stats
        known_last = 0 if full else (group.last_post_id or 0)
        owner_id = -abs(group.vk_id)

    with VKClient() as client:
        offset = 0
        highest_seen = known_last
        while True:
            resp = client.wall_get(owner_id=owner_id, offset=offset, count=page_size)
            items = (resp or {}).get("items") or []
            total = int((resp or {}).get("count") or 0)
            if not items:
                break
            # Filter to new posts if incremental.
            new_items = [it for it in items if int(it.get("id") or 0) > known_last]
            with session_scope() as s:
                group = s.get(Group, group_id)
                written = _upsert_posts(s, group, new_items)
            stats["fetched"] += len(items)
            stats["written"] += written
            stats["pages"] += 1
            for it in items:
                highest_seen = max(highest_seen, int(it.get("id") or 0))
            # Stop conditions.
            if not full and known_last and any(int(it.get("id") or 0) <= known_last for it in items):
                break
            offset += page_size
            if offset >= total:
                break
            if max_pages and stats["pages"] >= max_pages:
                break

    with session_scope() as s:
        s.execute(
            update(Group)
            .where(Group.id == group_id)
            .values(last_scraped_at=datetime.now(timezone.utc), last_post_id=highest_seen or None)
        )
    return stats


def scrape_groups_batch(group_ids: list[int]) -> dict[int, dict[str, int]]:
    """Use execute to fetch the first page of up to 25 groups in one round-trip.

    Best for incremental daily scans. Groups with >page_size new posts spill over
    to a follow-up incremental scan via scrape_group().
    """
    s_cfg = get_settings()
    batch = s_cfg.scrape_batch_size
    page = s_cfg.scrape_page_size
    results: dict[int, dict[str, int]] = {gid: {"fetched": 0, "written": 0} for gid in group_ids}

    with session_scope() as s:
        groups = {
            g.id: g
            for g in s.scalars(select(Group).where(Group.id.in_(group_ids), Group.active.is_(True))).all()
        }
    if not groups:
        return results

    with VKClient() as client:
        ordered = list(groups.values())
        for i in range(0, len(ordered), batch):
            chunk = ordered[i : i + batch]
            reqs = [(-abs(g.vk_id), 0, page) for g in chunk]
            responses = client.wall_get_batch(reqs)
            for g, resp in zip(chunk, responses):
                if not isinstance(resp, dict) or "items" not in resp:
                    continue
                items = resp.get("items") or []
                known_last = g.last_post_id or 0
                new_items = [it for it in items if int(it.get("id") or 0) > known_last]
                with session_scope() as s:
                    group = s.get(Group, g.id)
                    written = _upsert_posts(s, group, new_items)
                    highest = max(
                        [known_last] + [int(it.get("id") or 0) for it in items],
                        default=known_last,
                    )
                    s.execute(
                        update(Group)
                        .where(Group.id == g.id)
                        .values(
                            last_scraped_at=datetime.now(timezone.utc),
                            last_post_id=highest or None,
                        )
                    )
                results[g.id] = {"fetched": len(items), "written": written}
    return results


def refresh_metrics(post_ids: list[int]) -> int:
    """Re-fetch views/likes for given posts in batches of 100 via wall.getById."""
    if not post_ids:
        return 0
    updated = 0
    with session_scope() as s:
        posts = s.scalars(select(Post).where(Post.id.in_(post_ids))).all()
        keyed = {(p.owner_id, p.vk_post_id): p for p in posts}

    refs = [f"{oid}_{pid}" for (oid, pid) in keyed.keys()]
    with VKClient() as client:
        for i in range(0, len(refs), 100):
            chunk = refs[i : i + 100]
            resp = client.call("wall.getById", {"posts": ",".join(chunk)}) or []
            # 5.199 returns {"items": [...]}; older: list.
            items = resp.get("items") if isinstance(resp, dict) else resp
            for it in items or []:
                oid = int(it.get("owner_id") or 0)
                pid = int(it.get("id") or 0)
                post = keyed.get((oid, pid))
                if not post:
                    continue
                views, likes, reposts, comments = _extract_metrics(it)
                with session_scope() as s:
                    s.execute(
                        update(Post)
                        .where(Post.id == post.id)
                        .values(
                            views=views,
                            likes=likes,
                            reposts=reposts,
                            comments=comments,
                            updated_at=datetime.now(timezone.utc),
                        )
                    )
                    s.add(
                        PostMetricsHistory(
                            post_id=post.id,
                            views=views,
                            likes=likes,
                            reposts=reposts,
                            comments=comments,
                        )
                    )
                updated += 1
    return updated
