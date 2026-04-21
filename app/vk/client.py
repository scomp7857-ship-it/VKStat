"""Thin VK API client with token pooling, rate limiting, and execute batching."""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings


VK_API_BASE = "https://api.vk.com/method"


class VKError(RuntimeError):
    def __init__(self, code: int, message: str, payload: dict[str, Any] | None = None):
        super().__init__(f"VK error {code}: {message}")
        self.code = code
        self.message = message
        self.payload = payload or {}


class _RateLimiter:
    """Simple token-bucket: allow N requests per second per token."""

    def __init__(self, rps: float):
        self.min_interval = 1.0 / rps if rps > 0 else 0.0
        self._last = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._last + self.min_interval - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._last = now


@dataclass
class _TokenSlot:
    token: str
    limiter: _RateLimiter


class VKClient:
    """Round-robins VK tokens; each token has its own rate limiter bucket."""

    def __init__(self, tokens: list[str] | None = None, api_version: str | None = None):
        s = get_settings()
        tokens = tokens or s.vk_token_list
        if not tokens:
            raise RuntimeError("VK_TOKENS is empty. Set at least one service token in .env")
        self._slots = [_TokenSlot(t, _RateLimiter(s.vk_requests_per_second)) for t in tokens]
        self._cycle = itertools.cycle(self._slots)
        self._lock = threading.Lock()
        self.api_version = api_version or s.vk_api_version
        self._http = httpx.Client(timeout=30.0)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "VKClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _next_slot(self) -> _TokenSlot:
        with self._lock:
            return next(self._cycle)

    @retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception_type((httpx.HTTPError, VKError)),
    )
    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        slot = self._next_slot()
        slot.limiter.acquire()
        body = {**(params or {}), "access_token": slot.token, "v": self.api_version}
        resp = self._http.post(f"{VK_API_BASE}/{method}", data=body)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            err = data["error"]
            code = int(err.get("error_code", 0))
            # 6: too many requests, 29: rate limit reached -> retry
            # 10/15/100/113: transient data errors -> retry a couple times
            if code in {6, 10, 29}:
                raise VKError(code, err.get("error_msg", ""), err)
            # Non-retryable: raise a plain RuntimeError so tenacity doesn't retry.
            raise RuntimeError(f"VK error {code}: {err.get('error_msg', '')}")
        return data.get("response")

    def execute(self, code: str) -> Any:
        """Run a VKScript snippet via `execute`. Up to 25 nested API calls allowed."""
        return self.call("execute", {"code": code})

    # --- High-level helpers -------------------------------------------------

    def groups_get_by_id(self, keys: list[str]) -> list[dict[str, Any]]:
        """Resolve a list of screen_names or 'club<id>' strings to VK group objects.

        VK allows up to 500 ids per call.
        """
        out: list[dict[str, Any]] = []
        for i in range(0, len(keys), 500):
            chunk = keys[i : i + 500]
            resp = self.call(
                "groups.getById",
                {"group_ids": ",".join(chunk), "fields": "members_count,screen_name,name,type,is_closed"},
            )
            # 5.199 returns {"groups": [...]}, older formats return a list.
            if isinstance(resp, dict) and "groups" in resp:
                out.extend(resp["groups"])
            elif isinstance(resp, list):
                out.extend(resp)
        return out

    def wall_get(self, owner_id: int, offset: int = 0, count: int = 100) -> dict[str, Any]:
        return self.call(
            "wall.get",
            {"owner_id": owner_id, "offset": offset, "count": count, "extended": 0},
        )

    def wall_get_batch(self, requests: list[tuple[int, int, int]]) -> list[dict[str, Any]]:
        """Execute up to 25 wall.get calls in one API round-trip.

        `requests` items: (owner_id, offset, count).
        Returns a list aligned with `requests`; each item mirrors wall.get response
        or {"error": str} on a per-call failure.
        """
        if not requests:
            return []
        if len(requests) > 25:
            raise ValueError("execute allows at most 25 nested calls")
        parts = []
        for owner_id, offset, count in requests:
            parts.append(
                f"API.wall.get({{'owner_id': {owner_id}, 'offset': {offset}, 'count': {count}}})"
            )
        code = "return [" + ",".join(parts) + "];"
        resp = self.execute(code)
        if not isinstance(resp, list):
            return [{"error": "bad execute response"} for _ in requests]
        # `execute` returns `false` for failed sub-calls.
        return [r if isinstance(r, dict) else {"error": "sub-call failed"} for r in resp]
