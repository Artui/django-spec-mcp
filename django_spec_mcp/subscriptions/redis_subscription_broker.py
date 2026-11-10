from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from django_spec_mcp._compat.utils import namespace_digest, resolve_async_redis

AsyncRedis: Any = resolve_async_redis()

_DEFAULT_CHANNEL_PREFIX: str = "spec-mcp:sub"


class RedisSubscriptionBroker:
    """Cross-process topic fan-out over Redis pub/sub."""

    def __init__(
        self,
        client: Any,
        *,
        channel_prefix: str = _DEFAULT_CHANNEL_PREFIX,
        namespace: str | None = None,
    ) -> None:
        if AsyncRedis is None:  # pragma: no cover - exercised by the no-extras smoke job
            raise ImportError(
                "RedisSubscriptionBroker requires the `redis` package. "
                'Install with `pip install "django-spec-mcp[redis]"`.'
            )
        self._client: Any = client
        self._prefix: str = (
            channel_prefix
            if namespace is None
            else f"{channel_prefix}:{namespace_digest(namespace)}"
        )
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._pubsubs: dict[int, tuple[Any, list[str]]] = {}

    def _channel(self, topic: str) -> str:
        return f"{self._prefix}:{topic}"

    async def subscribe(self, topics: frozenset[str]) -> asyncio.Queue[Any]:
        """Register the channels **before returning**, then pump them."""
        queue: asyncio.Queue[Any] = asyncio.Queue()
        if not topics:
            return queue
        channels: list[str] = [self._channel(topic) for topic in sorted(topics)]
        pubsub = self._client.pubsub()
        await pubsub.subscribe(*channels)
        self._pubsubs[id(queue)] = (pubsub, channels)
        self._tasks[id(queue)] = asyncio.create_task(self._pump(pubsub, queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Any]) -> None:
        task: asyncio.Task[None] | None = self._tasks.pop(id(queue), None)
        if task is not None:
            task.cancel()
        entry = self._pubsubs.pop(id(queue), None)
        if entry is not None:
            pubsub, channels = entry
            # Scheduled rather than awaited: ``unsubscribe`` runs in a generator's ``finally``.
            asyncio.ensure_future(self._release(pubsub, channels))  # noqa: RUF006

    @property
    def active_subscriptions(self) -> int:
        return len(self._tasks)

    async def _release(self, pubsub: Any, channels: list[str]) -> None:
        """Give the channels back."""
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(*channels)
        with contextlib.suppress(Exception):
            await pubsub.aclose()

    async def publish(self, topic: str, payload: Any) -> int:
        """Publish to ``topic``'s channel; returns Redis's subscriber count."""
        message: bytes = json.dumps(payload).encode()
        receivers: int = await self._client.publish(self._channel(topic), message)
        return int(receivers)

    async def _pump(self, pubsub: Any, queue: asyncio.Queue[Any]) -> None:
        """Feed already-subscribed channels into this subscription's one queue."""
        async for message in pubsub.listen():  # pragma: no branch - exits via cancel
            if message.get("type") != "message":
                continue
            data: Any = message.get("data")
            if isinstance(data, bytes | bytearray):  # pragma: no branch - always bytes
                data = data.decode()
            await queue.put(json.loads(data))


__all__ = ["RedisSubscriptionBroker"]
