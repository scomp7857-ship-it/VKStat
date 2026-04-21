"""Resolve group URLs/screen names and upsert them into the database."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.dialects.postgresql import insert

from app.db import session_scope
from app.models import Group
from app.vk.client import VKClient
from app.vk.urls import GroupRef, parse_group_refs


@dataclass
class ImportResult:
    requested: int
    resolved: int
    inserted: int
    updated: int
    failed: list[str]


def import_groups(text: str, client: VKClient | None = None) -> ImportResult:
    refs = parse_group_refs(text)
    if not refs:
        return ImportResult(requested=0, resolved=0, inserted=0, updated=0, failed=[])

    keys = [r.screen_name or f"club{r.vk_id}" for r in refs]
    owns_client = client is None
    client = client or VKClient()
    try:
        resolved = client.groups_get_by_id(keys)
    finally:
        if owns_client:
            client.close()

    by_key = _index_by_key(refs, resolved)

    inserted = 0
    updated = 0
    failed: list[str] = []
    with session_scope() as s:
        for ref in refs:
            data = by_key.get(ref.key)
            if not data:
                failed.append(ref.raw)
                continue
            vk_id = int(data.get("id") or 0)
            if not vk_id:
                failed.append(ref.raw)
                continue
            screen_name = data.get("screen_name")
            url = f"https://vk.com/{screen_name or f'club{vk_id}'}"
            stmt = insert(Group).values(
                vk_id=vk_id,
                screen_name=screen_name,
                name=data.get("name"),
                type=data.get("type"),
                members_count=data.get("members_count"),
                is_closed=data.get("is_closed"),
                url=url,
                active=True,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[Group.vk_id],
                set_={
                    "screen_name": stmt.excluded.screen_name,
                    "name": stmt.excluded.name,
                    "type": stmt.excluded.type,
                    "members_count": stmt.excluded.members_count,
                    "is_closed": stmt.excluded.is_closed,
                    "url": stmt.excluded.url,
                    "active": True,
                },
            ).returning(Group.id, (Group.last_scraped_at.is_(None)).label("is_new"))
            row = s.execute(stmt).first()
            if row and row.is_new:
                inserted += 1
            else:
                updated += 1

    return ImportResult(
        requested=len(refs),
        resolved=len(resolved),
        inserted=inserted,
        updated=updated,
        failed=failed,
    )


def _index_by_key(refs: list[GroupRef], resolved: list[dict]) -> dict[str, dict]:
    by_id: dict[int, dict] = {}
    by_screen: dict[str, dict] = {}
    for item in resolved:
        gid = int(item.get("id") or 0)
        if gid:
            by_id[gid] = item
        sn = (item.get("screen_name") or "").lower()
        if sn:
            by_screen[sn] = item
    out: dict[str, dict] = {}
    for ref in refs:
        if ref.vk_id and ref.vk_id in by_id:
            out[ref.key] = by_id[ref.vk_id]
        elif ref.screen_name and ref.screen_name.lower() in by_screen:
            out[ref.key] = by_screen[ref.screen_name.lower()]
    return out
