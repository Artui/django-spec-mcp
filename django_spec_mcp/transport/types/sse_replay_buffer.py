from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SSEReplayBuffer(Protocol):
    """Pluggable per-session ring buffer for SSE event replay."""

    async def record(self, session_id: str, payload: Any) -> str:
        """Persist ``payload`` for ``session_id`` and return its event ID."""
        ...

    def replay(self, session_id: str, after_id: str | None) -> AsyncIterator[tuple[str, Any]]:
        """Yield ``(event_id, payload)`` pairs strictly after ``after_id``."""
        ...

    async def forget(self, session_id: str) -> None:
        """Drop all retained events for ``session_id``."""
        ...


__all__ = ["SSEReplayBuffer"]
