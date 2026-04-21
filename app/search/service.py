"""Search + TGStat-style analytics over posts using Postgres FTS and pg_trgm."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.search.query import CONFIG, ParsedQuery, parse_query


@dataclass
class SearchFilters:
    group_ids: list[int] | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


@dataclass
class SearchResult:
    total: int
    items: list[dict[str, Any]]


def _where_clause(pq: ParsedQuery, filters: SearchFilters) -> tuple[str, dict[str, Any]]:
    conds: list[str] = []
    params: dict[str, Any] = {}

    if pq.mode == "substring":
        conds.append("p.text ILIKE :sub")
        params["sub"] = f"%{pq.terms[0]}%"
    elif pq.mode == "phrase":
        tsq = " <-> ".join(pq.terms)
        conds.append(f"p.search_vector @@ phraseto_tsquery('{CONFIG}', :q)")
        params["q"] = " ".join(pq.terms)
    else:  # morph
        if pq.terms:
            conds.append(f"p.search_vector @@ plainto_tsquery('{CONFIG}', :q)")
            params["q"] = " ".join(pq.terms)

    if filters.group_ids:
        conds.append("p.group_id = ANY(:group_ids)")
        params["group_ids"] = filters.group_ids
    if filters.date_from:
        conds.append("p.date >= :date_from")
        params["date_from"] = filters.date_from
    if filters.date_to:
        conds.append("p.date <= :date_to")
        params["date_to"] = filters.date_to

    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return where, params


def search_posts(
    session: Session,
    q: str,
    filters: SearchFilters,
    *,
    limit: int = 50,
    offset: int = 0,
    order: str = "relevance",
) -> SearchResult:
    pq = parse_query(q)
    where, params = _where_clause(pq, filters)
    params["limit"] = limit
    params["offset"] = offset

    if pq.mode == "morph" and pq.terms:
        rank_expr = f"ts_rank_cd(p.search_vector, plainto_tsquery('{CONFIG}', :q))"
    elif pq.mode == "phrase":
        rank_expr = f"ts_rank_cd(p.search_vector, phraseto_tsquery('{CONFIG}', :q))"
    else:
        rank_expr = "0"

    order_sql = {
        "relevance": f"{rank_expr} DESC, p.date DESC",
        "date": "p.date DESC",
        "views": "p.views DESC NULLS LAST",
        "likes": "p.likes DESC NULLS LAST",
    }.get(order, f"{rank_expr} DESC, p.date DESC")

    headline_expr = (
        f"ts_headline('{CONFIG}', p.text, "
        f"{'plainto_tsquery' if pq.mode == 'morph' else 'phraseto_tsquery'}"
        f"('{CONFIG}', :q), 'MaxFragments=2,MaxWords=25,MinWords=5')"
        if pq.mode in {"morph", "phrase"} and pq.terms
        else "left(p.text, 300)"
    )

    sql = text(
        f"""
        SELECT
            p.id, p.group_id, p.text, p.date, p.views, p.likes, p.reposts, p.comments,
            p.url,
            g.name AS group_name, g.screen_name AS group_screen, g.url AS group_url,
            {rank_expr} AS rank,
            {headline_expr} AS headline
        FROM posts p
        JOIN groups g ON g.id = p.group_id
        {where}
        ORDER BY {order_sql}
        LIMIT :limit OFFSET :offset
        """
    )
    count_sql = text(f"SELECT count(*) FROM posts p {where}")
    if "group_ids" in params:
        sql = sql.bindparams(bindparam("group_ids", expanding=False))
        count_sql = count_sql.bindparams(bindparam("group_ids", expanding=False))

    total = session.execute(count_sql, params).scalar_one()
    rows = session.execute(sql, params).mappings().all()
    return SearchResult(total=int(total or 0), items=[dict(r) for r in rows])


def mentions_timeseries(
    session: Session,
    q: str,
    filters: SearchFilters,
    *,
    days: int = 30,
) -> list[dict[str, Any]]:
    pq = parse_query(q)
    if pq.is_empty:
        return []
    if not filters.date_from:
        filters.date_from = datetime.now(timezone.utc) - timedelta(days=days)
    where, params = _where_clause(pq, filters)
    sql = text(
        f"""
        SELECT date_trunc('day', p.date) AS day,
               count(*) AS mentions,
               coalesce(sum(p.views), 0) AS reach
        FROM posts p
        {where}
        GROUP BY 1
        ORDER BY 1
        """
    )
    rows = session.execute(sql, params).mappings().all()
    return [
        {"day": r["day"].date().isoformat(), "mentions": int(r["mentions"]), "reach": int(r["reach"])}
        for r in rows
    ]


def summary(session: Session, q: str, filters: SearchFilters) -> dict[str, int]:
    pq = parse_query(q)
    if pq.is_empty:
        return {"mentions": 0, "reach": 0, "likes": 0, "groups": 0}
    where, params = _where_clause(pq, filters)
    sql = text(
        f"""
        SELECT count(*) AS mentions,
               coalesce(sum(p.views), 0) AS reach,
               coalesce(sum(p.likes), 0) AS likes,
               count(DISTINCT p.group_id) AS groups
        FROM posts p
        {where}
        """
    )
    row = session.execute(sql, params).mappings().one()
    return {k: int(v) for k, v in row.items()}


def top_posts(
    session: Session,
    q: str,
    filters: SearchFilters,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    return search_posts(session, q, filters, limit=limit, offset=0, order="views").items


_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
_STOPWORDS = {
    # Tiny multi-lang stoplist; Postgres dictionaries already filter many, but
    # we re-tokenize raw text here for co-occurrence so add a common-word guard.
    "это", "как", "что", "для", "или", "при", "был", "так", "его", "она", "они", "мне",
    "так", "эта", "эти", "все", "над", "под", "над", "the", "and", "for", "that", "this",
    "про", "від", "для", "але", "що", "як", "чи", "мене", "тебе", "нас", "вас", "них",
    "также", "было", "есть", "нет",
}


def cooccurrence(
    session: Session,
    q: str,
    filters: SearchFilters,
    *,
    limit: int = 30,
    sample: int = 500,
) -> list[dict[str, Any]]:
    """Cheap word-cloud: tokenize top-matching posts and count frequencies."""
    pq = parse_query(q)
    if pq.is_empty:
        return []
    where, params = _where_clause(pq, filters)
    params["sample"] = sample
    sql = text(
        f"""
        SELECT p.text FROM posts p {where}
        ORDER BY p.date DESC
        LIMIT :sample
        """
    )
    rows = session.execute(sql, params).scalars().all()
    query_terms = {t.lower() for t in pq.terms}
    counts: dict[str, int] = {}
    for txt_ in rows:
        for word in _WORD_RE.findall((txt_ or "").lower()):
            if word in _STOPWORDS or word in query_terms:
                continue
            counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"word": w, "count": c} for w, c in ranked]
