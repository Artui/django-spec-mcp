from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis, FakeServer

from django_spec_mcp.subscriptions.redis_subscription_broker import RedisSubscriptionBroker

pytestmark = pytest.mark.asyncio


def _client() -> FakeAsyncRedis:
    return FakeAsyncRedis(server=FakeServer())


async def _await_subscriber(client: Any, channel: str, *, timeout: float = 1.0) -> None:
    """Wait until the listener task has actually subscribed."""
    deadline: float = asyncio.get_running_loop().time() + timeout
    while True:
        for ch, count in await client.pubsub_numsub(channel):
            name = ch.decode() if isinstance(ch, bytes) else ch
            if name == channel and count > 0:
                return
        if asyncio.get_running_loop().time() > deadline:  # pragma: no cover
            raise AssertionError(f"no subscriber on {channel}")
        await asyncio.sleep(0.005)


async def _drain(queue: asyncio.Queue[Any], *, timeout: float = 1.0) -> Any:
    return await asyncio.wait_for(queue.get(), timeout=timeout)


async def test_a_publish_from_another_worker_reaches_the_subscriber() -> None:
    """The write lands on one process and the stream is parked on another."""
    shared = FakeServer()
    listener, publisher_client = FakeAsyncRedis(server=shared), FakeAsyncRedis(server=shared)
    broker = RedisSubscriptionBroker(listener)
    queue = await broker.subscribe(frozenset({"resource:a"}))
    await _await_subscriber(listener, "spec-mcp:sub:resource:a")

    publisher = RedisSubscriptionBroker(publisher_client)
    assert await publisher.publish("resource:a", {"method": "m"}) >= 1
    assert await _drain(queue) == {"method": "m"}

    broker.unsubscribe(queue)
    await listener.aclose()
    await publisher_client.aclose()


async def test_one_subscription_listens_to_all_of_its_topics_at_once() -> None:
    """One listener task per *subscription*, not per topic."""
    client = _client()
    broker = RedisSubscriptionBroker(client)
    queue = await broker.subscribe(frozenset({"a", "b"}))
    await _await_subscriber(client, "spec-mcp:sub:a")
    await _await_subscriber(client, "spec-mcp:sub:b")
    assert len(broker._tasks) == 1

    await broker.publish("a", 1)
    await broker.publish("b", 2)
    assert {await _drain(queue), await _drain(queue)} == {1, 2}

    broker.unsubscribe(queue)
    await client.aclose()


async def test_publishing_to_a_topic_nobody_watches_reports_zero() -> None:
    client = _client()
    broker = RedisSubscriptionBroker(client)
    assert await broker.publish("quiet", {}) == 0
    await client.aclose()


async def test_unsubscribing_cancels_the_listener() -> None:
    """Without this the task and its channel subscriptions outlive the connection."""
    client = _client()
    broker = RedisSubscriptionBroker(client)
    queue = await broker.subscribe(frozenset({"a"}))
    await _await_subscriber(client, "spec-mcp:sub:a")
    broker.unsubscribe(queue)
    await asyncio.sleep(0.05)
    assert broker._tasks == {}
    await client.aclose()


async def test_unsubscribing_twice_is_a_no_op() -> None:
    client = _client()
    broker = RedisSubscriptionBroker(client)
    queue = await broker.subscribe(frozenset({"a"}))
    broker.unsubscribe(queue)
    broker.unsubscribe(queue)
    await client.aclose()


async def test_a_subscription_naming_no_topics_starts_no_listener() -> None:
    """An empty filter is legal but odd; the acknowledgement tells the client it hears nothing."""
    client = _client()
    broker = RedisSubscriptionBroker(client)
    queue = await broker.subscribe(frozenset())
    assert broker._tasks == {}
    assert isinstance(queue, asyncio.Queue)
    broker.unsubscribe(queue)
    await client.aclose()


async def test_the_channel_prefix_is_configurable() -> None:
    """Two servers sharing one Redis must not share a topic space."""
    client = _client()
    broker = RedisSubscriptionBroker(client, channel_prefix="other")
    queue = await broker.subscribe(frozenset({"a"}))
    await _await_subscriber(client, "other:a")
    broker.unsubscribe(queue)
    await client.aclose()


async def test_subscribe_acknowledgement_frames_are_not_delivered_as_payloads() -> None:
    """Redis emits a ``subscribe`` control frame on the same stream."""
    client = _client()
    broker = RedisSubscriptionBroker(client)
    queue = await broker.subscribe(frozenset({"a"}))
    await _await_subscriber(client, "spec-mcp:sub:a")
    await broker.publish("a", {"real": True})
    assert await _drain(queue) == {"real": True}
    broker.unsubscribe(queue)
    await client.aclose()


async def test_constructing_without_the_extra_is_a_clear_error(monkeypatch: Any) -> None:
    """redis is optional, so importing the module must work without it."""
    import django_spec_mcp.subscriptions.redis_subscription_broker as module

    monkeypatch.setattr(module, "AsyncRedis", None)
    with pytest.raises(ImportError, match=r"\[redis\]"):
        module.RedisSubscriptionBroker(object())


async def test_subscribe_returns_only_once_the_channels_are_live() -> None:
    """Registering in a background task would ack before publishes could reach the stream."""
    shared = FakeServer()
    listener, publisher = FakeAsyncRedis(server=shared), FakeAsyncRedis(server=shared)
    broker = RedisSubscriptionBroker(listener)
    queue = await broker.subscribe(frozenset({"a"}))

    assert await RedisSubscriptionBroker(publisher).publish("a", {"n": 1}) >= 1
    assert await _drain(queue) == {"n": 1}

    broker.unsubscribe(queue)
    await listener.aclose()
    await publisher.aclose()


async def test_the_channels_are_given_back_when_a_subscription_unwinds() -> None:
    """Observable now that release is a real coroutine, not a suppressed block in a finally."""
    client = _client()
    broker = RedisSubscriptionBroker(client)
    queue = await broker.subscribe(frozenset({"a"}))
    assert (await client.pubsub_numsub("spec-mcp:sub:a"))[0][1] == 1

    broker.unsubscribe(queue)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if (await client.pubsub_numsub("spec-mcp:sub:a"))[0][1] == 0:
            break
    assert (await client.pubsub_numsub("spec-mcp:sub:a"))[0][1] == 0
    await client.aclose()


async def test_active_subscriptions_counts_this_workers_streams() -> None:
    """What ``MAX_CONCURRENT_SUBSCRIPTIONS`` bounds."""
    client = _client()
    broker = RedisSubscriptionBroker(client)
    assert broker.active_subscriptions == 0
    one = await broker.subscribe(frozenset({"a"}))
    two = await broker.subscribe(frozenset({"b"}))
    assert broker.active_subscriptions == 2
    broker.unsubscribe(one)
    assert broker.active_subscriptions == 1
    broker.unsubscribe(two)
    await client.aclose()


async def test_two_servers_on_one_redis_do_not_share_a_topic_space() -> None:
    """Topic names are built from caller-supplied values — a notification kind, a resource URI."""
    shared = FakeServer()
    public = RedisSubscriptionBroker(FakeAsyncRedis(server=shared), namespace="public")
    internal = RedisSubscriptionBroker(FakeAsyncRedis(server=shared), namespace="internal")

    queue = await public.subscribe(frozenset({"resource:invoices://1"}))
    await _await_subscriber(
        public._client,  # noqa: SLF001
        public._channel("resource:invoices://1"),  # noqa: SLF001
    )
    assert await internal.publish("resource:invoices://1", {"n": 1}) == 0
    assert queue.empty()

    assert await public.publish("resource:invoices://1", {"n": 2}) == 1
    assert await _drain(queue) == {"n": 2}

    public.unsubscribe(queue)
    await public._client.aclose()  # noqa: SLF001
    await internal._client.aclose()  # noqa: SLF001
