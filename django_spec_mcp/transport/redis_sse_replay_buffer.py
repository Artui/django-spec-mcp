from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from django_spec_mcp._compat.utils import namespace_digest, resolve_async_redis

AsyncRedis: Any = resolve_async_redis()


_DEFAULT_KEY_PREFIX: str = "spec-mcp:sse-replay"
# A stream is only worth keeping while its session could still reconnect to it,
# and the session store's own idle window is that horizon.
_DEFAULT_TTL_SECONDS: int = 60 * 60 * 24


class RedisSSEReplayBuffer:
    """Cross-process replay buffer backed by Redis Streams."""

    def __init__(
        self,
        client: Any,
        *,
        max_events: int = 1024,
        key_prefix: str = _DEFAULT_KEY_PREFIX,
        namespace: str | None = None,
        ttl_seconds: int | None = _DEFAULT_TTL_SECONDS,
    ) -> None:
        if AsyncRedis is None:  # pragma: no cover - exercised by the no-extras smoke job
            raise ImportError(
                "RedisSSEReplayBuffer requires the `redis` package. "
                'Install with `pip install "django-spec-mcp[redis]"`.'
            )
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive, or None to keep streams forever")
        self._client: Any = client
        self._max_events: int = max_events
        self._prefix: str = (
            key_prefix if namespace is None else f"{key_prefix}:{namespace_digest(namespace)}"
        )
        self._ttl_seconds: int | None = ttl_seconds

    def _key(self, session_id: str) -> str:
        return f"{self._prefix}:{session_id}"

    async def record(self, session_id: str, payload: Any) -> str:
        """Append ``payload`` to the session's stream and return the assigned ID."""
        body: str = json.dumps(payload)
        key: str = self._key(session_id)
        raw_id: Any = await self._client.xadd(
            key,
            {"data": body},
            maxlen=self._max_events,
            approximate=True,
        )
        if self._ttl_seconds is not None:
            await self._client.expire(key, self._ttl_seconds)
        if isinstance(raw_id, bytes | bytearray):
            return raw_id.decode()
        return str(raw_id)  # pragma: no cover - real & fake redis both return bytes

    async def replay(self, session_id: str, after_id: str | None) -> AsyncIterator[tuple[str, Any]]:
        if after_id is None:
            return
        # The exclusive lower bound yields every entry strictly greater than ``after_id``.
        entries: Any = await self._client.xrange(self._key(session_id), min=f"({after_id}")
        for raw_id, fields in entries:
            event_id: str = (
                raw_id.decode() if isinstance(raw_id, bytes | bytearray) else str(raw_id)
            )
            data: Any = fields.get(b"data") or fields.get("data")
            if isinstance(  # pragma: no branch - real & fake redis both yield bytes
                data, bytes | bytearray
            ):
                data = data.decode()
            yield event_id, json.loads(data)

    async def forget(self, session_id: str) -> None:
        await self._client.delete(self._key(session_id))


__all__ = ["RedisSSEReplayBuffer"]
