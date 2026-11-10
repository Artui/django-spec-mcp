from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass

from django.core.cache import cache

from django_spec_mcp._compat.utils import namespace_digest
from django_spec_mcp.conf import get_setting


@dataclass(frozen=True)
class _Entry:
    """What a session key holds: the owning principal, and when it was minted."""

    principal_id: str
    created_at: float


_KEY_PREFIX: str = "spec-mcp:session:"
# The alphabet ``secrets.token_urlsafe`` draws from, with a ceiling well under
# the 250-byte cache-key limit memcached enforces: the shape of every id
# ``create`` hands out, and so the only shape worth a cache round-trip.
_SESSION_ID_SHAPE = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")


class DjangoCacheSessionStore:
    """Session store backed by ``django.core.cache``."""

    def __init__(
        self,
        *,
        namespace: str | None = None,
        ttl_seconds: int | None = None,
        max_age_seconds: int | None = None,
    ) -> None:
        # Folded into the prefix once: the namespace is fixed for the store's lifetime.
        self._prefix: str = (
            _KEY_PREFIX if namespace is None else f"{_KEY_PREFIX}{namespace_digest(namespace)}:"
        )
        self._ttl_seconds: int = (
            ttl_seconds if ttl_seconds is not None else get_setting("SESSION_TTL_SECONDS")
        )
        self._max_age_seconds: int | None = (
            max_age_seconds
            if max_age_seconds is not None
            else get_setting("SESSION_MAX_AGE_SECONDS")
        )

    def _key(self, session_id: str) -> str:
        return f"{self._prefix}{session_id}"

    def _readable(self, session_id: str) -> bool:
        """Whether the id could be one this store minted."""
        return bool(_SESSION_ID_SHAPE.match(session_id))

    def create(self, *, principal_id: str) -> str:
        token: str = secrets.token_urlsafe(24)
        cache.set(
            self._key(token),
            _Entry(principal_id=principal_id, created_at=time.time()),
            timeout=self._ttl_seconds,
        )
        return token

    def exists(self, session_id: str) -> bool:
        """Whether a key is present — deliberately *not* ``owner() is not None``."""
        return self._readable(session_id) and cache.get(self._key(session_id)) is not None

    def owner(self, session_id: str) -> str | None:
        """Resolve the owning principal, refreshing the idle window on the way."""
        if not self._readable(session_id):
            return None
        entry = cache.get(self._key(session_id))
        if isinstance(entry, str):
            entry = _Entry(principal_id=entry, created_at=time.time())
        if not isinstance(entry, _Entry):
            return None
        remaining = self._remaining_lifetime(entry)
        if remaining <= 0:
            cache.delete(self._key(session_id))
            return None
        cache.set(self._key(session_id), entry, timeout=remaining)
        return entry.principal_id

    def _remaining_lifetime(self, entry: _Entry) -> int:
        """The idle window, clipped so the session cannot outlive its max age."""
        if self._max_age_seconds is None:
            return self._ttl_seconds
        left = int(entry.created_at + self._max_age_seconds - time.time())
        return min(self._ttl_seconds, left)

    def destroy(self, session_id: str) -> None:
        if not self._readable(session_id):
            return
        cache.delete(self._key(session_id))


__all__ = ["DjangoCacheSessionStore"]
