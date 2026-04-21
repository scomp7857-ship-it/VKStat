"""Build Postgres FTS queries. Supports phrase, morphological, and substring modes."""
from __future__ import annotations

import re
from dataclasses import dataclass


CONFIG = "vkstat_ru_uk"


@dataclass
class ParsedQuery:
    raw: str
    # One of: "phrase" (quoted), "morph" (default), "substring" (`~...~`).
    mode: str
    terms: list[str]

    @property
    def is_empty(self) -> bool:
        return not self.terms


_TOKEN_RE = re.compile(r"[\w\-]+", re.UNICODE)


def parse_query(q: str) -> ParsedQuery:
    q = (q or "").strip()
    if not q:
        return ParsedQuery(raw="", mode="morph", terms=[])

    if q.startswith('"') and q.endswith('"') and len(q) >= 2:
        inner = q[1:-1]
        return ParsedQuery(raw=q, mode="phrase", terms=_TOKEN_RE.findall(inner))

    if q.startswith("~") and q.endswith("~") and len(q) >= 2:
        return ParsedQuery(raw=q, mode="substring", terms=[q[1:-1]])

    return ParsedQuery(raw=q, mode="morph", terms=_TOKEN_RE.findall(q))
