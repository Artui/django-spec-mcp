from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import AsyncIterator
from typing import Any


class InMemorySSEReplayBuffer:
    """In-process bounded replay buffer for SSE event resume."""

    def __init__(self, *, max_events: int = 1024, max_sessions: int = 1024) -> None:
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        if max_sessions <= 0:
            raise ValueError("max_sessions must be positive")
        self._max_events: int = max_events
        self._max_sessions: int = max_sessions
        # Ordered by least-recently-written, so the oldest entry is the one to drop.
        self._buffers: OrderedDict[str, deque[tuple[str, Any]]] = OrderedDict()
        self._counters: dict[str, int] = {}

    async def record(self, session_id: str, payload: Any) -> str:
        next_id: int = self._counters.get(session_id, 0) + 1
        self._counters[session_id] = next_id
        # Zero-padded so textual ordering matches numeric ordering.
        event_id: str = f"{next_id:016d}"
        ring: deque[tuple[str, Any]] | None = self._buffers.get(session_id)
        if ring is None:
            ring = deque(maxlen=self._max_events)
            self._buffers[session_id] = ring
        else:
            self._buffers.move_to_end(session_id)
        ring.append((event_id, payload))
        while len(self._buffers) > self._max_sessions:
            stale, _ = self._buffers.popitem(last=False)
            self._counters.pop(stale, None)
        return event_id

    async def replay(self, session_id: str, after_id: str | None) -> AsyncIterator[tuple[str, Any]]:
        if after_id is None:
            return
        ring: deque[tuple[str, Any]] | None = self._buffers.get(session_id)
        if ring is None:
            return
        # ``after_id`` is the last ID the client *did* see.
        for event_id, payload in list(ring):
            if event_id > after_id:
                yield event_id, payload

    async def forget(self, session_id: str) -> None:
        self._buffers.pop(session_id, None)
        self._counters.pop(session_id, None)


__all__ = ["InMemorySSEReplayBuffer"]
