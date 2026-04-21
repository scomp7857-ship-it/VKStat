"""Thin CLI wrappers. Invoked via `python -m app.cli <cmd>`."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

from app.db import session_scope
from app.models import Group
from app.queue import get_queue
from app.config import get_settings
from app.scraper.groups import import_groups


def _cmd_import(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 2
    result = import_groups(path.read_text(encoding="utf-8"))
    print(
        f"requested={result.requested} resolved={result.resolved} "
        f"inserted={result.inserted} updated={result.updated} "
        f"failed={len(result.failed)}"
    )
    if result.failed:
        print("Failed:", *result.failed, sep="\n  ")
    return 0


def _cmd_enqueue_scrape(args: argparse.Namespace) -> int:
    cfg = get_settings()
    q = get_queue(cfg.queue_scrape)
    with session_scope() as s:
        ids = [r[0] for r in s.execute(select(Group.id).where(Group.active.is_(True))).all()]
    for gid in ids:
        q.enqueue("app.jobs.job_scrape_group", gid, full=args.full, job_timeout=3600)
    print(f"Enqueued {len(ids)} scrape jobs (full={args.full}).")
    return 0


def _cmd_enqueue_refresh(args: argparse.Namespace) -> int:
    cfg = get_settings()
    q = get_queue(cfg.queue_metrics)
    q.enqueue("app.jobs.job_refresh_metrics_fresh", job_timeout=3600)
    q.enqueue("app.jobs.job_refresh_metrics_old", job_timeout=3600)
    print("Enqueued fresh + old metrics refresh.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vkstat")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_import = sub.add_parser("import-groups", help="Import groups from a text file of URLs.")
    p_import.add_argument("file")
    p_import.set_defaults(func=_cmd_import)

    p_scrape = sub.add_parser("enqueue-scrape", help="Enqueue scrape jobs for all active groups.")
    p_scrape.add_argument("--full", action="store_true", help="Full wall backfill.")
    p_scrape.set_defaults(func=_cmd_enqueue_scrape)

    p_refresh = sub.add_parser("enqueue-refresh", help="Enqueue metrics-refresh jobs.")
    p_refresh.set_defaults(func=_cmd_enqueue_refresh)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
