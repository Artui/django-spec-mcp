from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from django_spec_mcp._compat.utils import resolve_async_redis

# ``redis`` is an optional extra, so importing this module without it must not
# crash the package; the ImportError fires only when a consumer constructs a
# ``RedisSSEBroker``.
AsyncRedis: Any = resolve_async_redis()


_DEFAULT_CHANNEL_PREFIX: str = "spec-mcp:sse"


class RedisSSEBroker:
    """Cross-process SSE broker backed by Redis pub/sub."""

    def __init__(
        self,
        client: Any,
        *,
        channel_prefix: str = _DEFAULT_CHANNEL_PREFIX,
        max_queued_events: int = 1024,
    ) -> None:
        if AsyncRedis is None:  # pragma: no cover - exercised by the no-extras smoke job
            raise ImportError(
                "RedisSSEBroker requires the `redis` package. "
                'Install with `pip install "django-spec-mcp[redis]"`.'
            )
        if max_queued_events <= 0:
            raise ValueError("max_queued_events must be positive")
        self._client: Any = client
        self._prefix: str = channel_prefix
        self._max_queued_events: int = max_queued_events
        # Per-session listener tasks plus the queues they feed.
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._queues: dict[str, asyncio.Queue[Any]] = {}

    def _channel(self, session_id: str) -> str:
        return f"{self._prefix}:{session_id}"

    def subscribe(self, session_id: str) -> asyncio.Queue[Any]:
        # Re-subscribe replaces cleanly, mirroring the in-memory broker.
        existing: asyncio.Task[None] | None = self._tasks.pop(session_id, None)
        if existing is not None:
            existing.cancel()

        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._max_queued_events)
        self._queues[session_id] = queue
        self._tasks[session_id] = asyncio.create_task(self._listen(session_id, queue))
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[Any]) -> None:
        current: asyncio.Queue[Any] | None = self._queues.get(session_id)
        if current is not queue:
            return  # stale unsubscribe from a replaced subscriber — no-op.
        self._queues.pop(session_id, None)
        task: asyncio.Task[None] | None = self._tasks.pop(session_id, None)
        if task is not None:  # pragma: no branch - subscribe always pairs queue+task
            task.cancel()

    async def publish(self, session_id: str, payload: Any) -> bool:
        """Publish to the session's channel and report whether anyone received it."""
        message: bytes = json.dumps(payload).encode()
        receivers: int = await self._client.publish(self._channel(session_id), message)
        return receivers > 0

    def has_subscriber(self, session_id: str) -> bool:
        """Local-only check: whether *this* worker has an active subscriber."""
        return session_id in self._queues

    @property
    def active_streams(self) -> int:
        """Local subscribers only, matching what the cap protects."""
        return len(self._queues)

    async def _listen(self, session_id: str, queue: asyncio.Queue[Any]) -> None:
        """Pump messages from Redis pub/sub into the per-session queue."""
        pubsub = self._client.pubsub()
        try:
            await pubsub.subscribe(self._channel(session_id))
            async for message in pubsub.listen():  # pragma: no branch - loop exits via cancel
                if message.get("type") != "message":
                    continue
                data: Any = message.get("data")
                if isinstance(
                    data, bytes | bytearray
                ):  # pragma: no branch - fakeredis always bytes
                    data = data.decode()
                _offer(queue, json.loads(data))
        except asyncio.CancelledError:
            raise
        finally:
            with contextlib.suppress(Exception):  # pragma: no cover
                await pubsub.unsubscribe(self._channel(session_id))
            with contextlib.suppress(Exception):  # pragma: no cover
                await pubsub.aclose()


def _offer(queue: asyncio.Queue[Any], payload: Any) -> None:
    """Enqueue ``payload``, evicting the oldest if the reader has stalled."""
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(payload)


__all__ = ["RedisSSEBroker"]
