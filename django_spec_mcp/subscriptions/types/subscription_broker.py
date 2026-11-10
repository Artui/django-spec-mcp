from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SubscriptionBroker(Protocol):
    """Topic-keyed pub/sub for notifications, with **many** subscribers per topic."""

    async def subscribe(self, topics: frozenset[str]) -> asyncio.Queue[Any]: ...

    def unsubscribe(self, queue: asyncio.Queue[Any]) -> None: ...

    async def publish(self, topic: str, payload: Any) -> int: ...

    @property
    def active_subscriptions(self) -> int:
        """How many subscriptions this broker is currently feeding."""
        ...


__all__ = ["SubscriptionBroker"]
