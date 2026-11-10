from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SSEBroker(Protocol):
    """Pluggable pub/sub for server-pushed MCP messages."""

    def subscribe(self, session_id: str) -> asyncio.Queue[Any]: ...

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[Any]) -> None: ...

    async def publish(self, session_id: str, payload: Any) -> bool: ...

    def has_subscriber(self, session_id: str) -> bool: ...

    @property
    def active_streams(self) -> int:
        """How many session streams this worker is currently serving."""
        ...


__all__ = ["SSEBroker"]
