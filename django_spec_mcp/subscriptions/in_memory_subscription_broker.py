from __future__ import annotations

import asyncio
from typing import Any


class InMemorySubscriptionBroker:
    """In-process topic fan-out, for development and tests."""

    def __init__(self) -> None:
        # Instance state, never module-level: two servers keep separate subscriptions.
        self._by_topic: dict[str, set[asyncio.Queue[Any]]] = {}
        self._topics_by_queue: dict[int, tuple[asyncio.Queue[Any], frozenset[str]]] = {}

    async def subscribe(self, topics: frozenset[str]) -> asyncio.Queue[Any]:
        # Nothing to await — registration is a dict write.
        queue: asyncio.Queue[Any] = asyncio.Queue()
        for topic in topics:
            self._by_topic.setdefault(topic, set()).add(queue)
        self._topics_by_queue[id(queue)] = (queue, topics)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Any]) -> None:
        entry = self._topics_by_queue.pop(id(queue), None)
        if entry is None:
            return
        _, topics = entry
        for topic in topics:
            # Always present: ``subscribe`` registered this queue under every one of these topics.
            subscribers = self._by_topic[topic]
            subscribers.discard(queue)
            if not subscribers:
                del self._by_topic[topic]

    @property
    def active_subscriptions(self) -> int:
        return len(self._topics_by_queue)

    async def publish(self, topic: str, payload: Any) -> int:
        """Deliver to every subscriber of ``topic``; returns how many got it."""
        subscribers = self._by_topic.get(topic)
        if not subscribers:
            return 0
        for queue in tuple(subscribers):
            await queue.put(payload)
        return len(subscribers)


__all__ = ["InMemorySubscriptionBroker"]
