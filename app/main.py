"""FastAPI app: HTMX UI + JSON endpoints for search and analytics."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Group
from app.queue import get_queue
from app.scraper.groups import import_groups
from app.search.service import (
    SearchFilters,
    cooccurrence,
    mentions_timeseries,
    search_posts,
    summary,
    top_posts,
)


BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="VKStat", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _filters(
    group_ids: list[int] | None,
    date_from: str | None,
    date_to: str | None,
) -> SearchFilters:
    return SearchFilters(
        group_ids=group_ids or None,
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to),
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)) -> Any:
    groups = db.scalars(select(Group).order_by(Group.added_at.desc()).limit(200)).all()
    total_groups = db.scalar(select(func.count(Group.id))) or 0
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "groups": groups, "total_groups": total_groups},
    )


@app.post("/groups/import", response_class=HTMLResponse)
def groups_import(request: Request, urls: str = Form(...)) -> Any:
    if not get_settings().vk_token_list:
        return HTMLResponse(
            "<div class='error'>VK_TOKENS not configured. Edit .env.</div>", status_code=400
        )
    result = import_groups(urls)
    # Also enqueue initial full scrape for newly-added groups.
    q = get_queue(get_settings().queue_scrape)
    from app.db import session_scope  # local import to avoid cycle

    with session_scope() as s:
        ids = [r[0] for r in s.execute(select(Group.id).where(Group.active.is_(True))).all()]
    for gid in ids:
        q.enqueue("app.jobs.job_scrape_group", gid, full=False, job_timeout=3600)
    return templates.TemplateResponse(
        "_import_result.html", {"request": request, "result": result}
    )


@app.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    q: str = "",
    order: str = "relevance",
    group_id: list[int] | None = Query(default=None),
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
) -> Any:
    filters = _filters(group_id, date_from, date_to)
    summary_data = summary(db, q, filters) if q else None
    timeseries = mentions_timeseries(db, q, filters) if q else []
    top = top_posts(db, q, filters, limit=10) if q else []
    cloud = cooccurrence(db, q, filters, limit=40) if q else []
    results = search_posts(db, q, filters, limit=50, order=order) if q else None
    groups = db.scalars(select(Group).order_by(Group.name).limit(500)).all()
    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "q": q,
            "order": order,
            "group_id": group_id or [],
            "date_from": date_from or "",
            "date_to": date_to or "",
            "summary": summary_data,
            "timeseries": timeseries,
            "top": top,
            "cloud": cloud,
            "results": results,
            "groups": groups,
        },
    )


@app.get("/api/search")
def api_search(
    q: str,
    order: str = "relevance",
    group_id: list[int] | None = Query(default=None),
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> JSONResponse:
    filters = _filters(group_id, date_from, date_to)
    res = search_posts(db, q, filters, limit=limit, offset=offset, order=order)
    return JSONResponse(
        {
            "total": res.total,
            "items": [_jsonify(i) for i in res.items],
        }
    )


@app.get("/api/analytics")
def api_analytics(
    q: str,
    group_id: list[int] | None = Query(default=None),
    date_from: str | None = None,
    date_to: str | None = None,
    days: int = 30,
    db: Session = Depends(get_db),
) -> JSONResponse:
    filters = _filters(group_id, date_from, date_to)
    return JSONResponse(
        {
            "summary": summary(db, q, filters),
            "timeseries": mentions_timeseries(db, q, filters, days=days),
            "top": [_jsonify(i) for i in top_posts(db, q, filters, limit=10)],
            "cooccurrence": cooccurrence(db, q, filters, limit=40),
        }
    )


@app.get("/export.csv")
def export_csv(
    q: str,
    order: str = "relevance",
    group_id: list[int] | None = Query(default=None),
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    filters = _filters(group_id, date_from, date_to)
    res = search_posts(db, q, filters, limit=5000, order=order)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "group", "url", "views", "likes", "reposts", "comments", "text"])
    for r in res.items:
        w.writerow(
            [
                r["date"].isoformat() if r.get("date") else "",
                r.get("group_name") or r.get("group_screen") or "",
                r.get("url") or "",
                r.get("views") or 0,
                r.get("likes") or 0,
                r.get("reposts") or 0,
                r.get("comments") or 0,
                (r.get("text") or "").replace("\n", " ").strip(),
            ]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="vkstat.csv"'},
    )


def _jsonify(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    if isinstance(out.get("date"), datetime):
        out["date"] = out["date"].isoformat()
    return out
