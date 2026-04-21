"""Shared Redis / RQ queue factory."""
from __future__ import annotations

from functools import lru_cache

from redis import Redis
from rq import Queue

from app.config import get_settings


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url)


def get_queue(name: str | None = None) -> Queue:
    cfg = get_settings()
    return Queue(name or cfg.queue_default, connection=get_redis())
