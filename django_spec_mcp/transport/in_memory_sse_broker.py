from __future__ import annotations

import asyncio
from typing import Any


class InMemorySSEBroker:
    """In-process per-session pub/sub for server-pushed MCP messages."""

    def __init__(self, *, max_queued_events: int = 1024) -> None:
        if max_queued_events <= 0:
            raise ValueError("max_queued_events must be positive")
        self._max_queued_events: int = max_queued_events
        self._queues: dict[str, asyncio.Queue[Any]] = {}

    def subscribe(self, session_id: str) -> asyncio.Queue[Any]:
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._max_queued_events)
        self._queues[session_id] = queue
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[Any]) -> None:
        """Remove ``queue`` from the registry if it's still the live subscriber."""
        current: asyncio.Queue[Any] | None = self._queues.get(session_id)
        if current is queue:
            self._queues.pop(session_id, None)

    async def publish(self, session_id: str, payload: Any) -> bool:
        """Enqueue ``payload`` for ``session_id`` if a subscriber exists."""
        queue: asyncio.Queue[Any] | None = self._queues.get(session_id)
        if queue is None:
            return False
        if queue.full():
            queue.get_nowait()
            queue.put_nowait(payload)
            return False
        queue.put_nowait(payload)
        return True

    def has_subscriber(self, session_id: str) -> bool:
        return session_id in self._queues

    @property
    def active_streams(self) -> int:
        return len(self._queues)


__all__ = ["InMemorySSEBroker"]
