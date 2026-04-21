"""Parse assorted VK group URL/screen-name formats into resolvable identifiers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


_HOST_RE = re.compile(r"^(m\.|www\.)?vk\.(com|ru)$", re.IGNORECASE)
_NUMERIC_CLUB_RE = re.compile(r"^(club|public|event|group)(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class GroupRef:
    raw: str
    # Either `screen_name` or numeric `vk_id` will be set.
    screen_name: str | None = None
    vk_id: int | None = None

    @property
    def key(self) -> str:
        return self.screen_name or (f"club{self.vk_id}" if self.vk_id else self.raw)


def parse_group_ref(value: str) -> GroupRef | None:
    value = value.strip()
    if not value:
        return None

    # Strip scheme-less URLs.
    candidate = value
    if "://" not in candidate and candidate.startswith("vk."):
        candidate = "https://" + candidate

    if "://" in candidate:
        parsed = urlparse(candidate)
        if not _HOST_RE.match(parsed.netloc or ""):
            return None
        path = (parsed.path or "/").strip("/").split("/", 1)[0]
    else:
        path = value.lstrip("@").strip("/")

    if not path:
        return None

    m = _NUMERIC_CLUB_RE.match(path)
    if m:
        return GroupRef(raw=value, vk_id=int(m.group(2)))

    # Screen name like "durov" or "team".
    if re.fullmatch(r"[A-Za-z0-9_\.]+", path):
        return GroupRef(raw=value, screen_name=path)

    return None


def parse_group_refs(text: str) -> list[GroupRef]:
    out: list[GroupRef] = []
    seen: set[str] = set()
    for line in re.split(r"[\s,;]+", text or ""):
        ref = parse_group_ref(line)
        if ref and ref.key not in seen:
            seen.add(ref.key)
            out.append(ref)
    return out
