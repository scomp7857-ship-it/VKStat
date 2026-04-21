"""RQ worker: consumes scrape + metrics queues."""
from __future__ import annotations

import logging

from rq import Worker

from app.config import get_settings
from app.queue import get_queue, get_redis


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = get_settings()
    queues = [get_queue(cfg.queue_scrape), get_queue(cfg.queue_metrics), get_queue(cfg.queue_default)]
    Worker(queues, connection=get_redis()).work(with_scheduler=False)


if __name__ == "__main__":
    main()
