from __future__ import annotations

import time
from collections.abc import Callable

from django.core.cache import cache
from django.http import HttpRequest

from django_spec_mcp.auth.rate_limits.utils import default_key, require_positive
from django_spec_mcp.auth.types.token_info import TokenInfo

# Distinct from the other limiters' prefixes so an operator can tell which
# scheme produced a counter from the cache key alone.
_DEFAULT_KEY_PREFIX: str = "spec-mcp:rl-sw"


class SlidingWindowRateLimit:
    """Sliding-window rate limiter using a list of timestamps in cache."""

    def __init__(
        self,
        *,
        max_calls: int,
        per_seconds: int,
        namespace: str = "default",
        key: Callable[[HttpRequest, TokenInfo], str] | None = None,
    ) -> None:
        require_positive("max_calls", max_calls)
        require_positive("per_seconds", per_seconds)
        self._max: int = max_calls
        self._window: int = per_seconds
        self._namespace: str = namespace
        self._key_fn: Callable[[HttpRequest, TokenInfo], str] = key or default_key

    def consume(self, request: HttpRequest, token: TokenInfo) -> int | None:
        now: float = time.time()
        cutoff: float = now - self._window
        cache_key: str = f"{_DEFAULT_KEY_PREFIX}:{self._namespace}:{self._key_fn(request, token)}"
        timestamps: list[float] = cache.get(cache_key) or []
        live: list[float] = [ts for ts in timestamps if ts > cutoff]
        if len(live) >= self._max:
            # The oldest live timestamp is when the window regains capacity.
            retry_at: float = live[0] + self._window
            retry_after: int = max(int(retry_at - now), 1)
            cache.set(cache_key, live, timeout=self._window)
            return retry_after
        live.append(now)
        cache.set(cache_key, live, timeout=self._window)
        return None


__all__ = ["SlidingWindowRateLimit"]
