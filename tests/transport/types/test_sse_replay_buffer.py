"""What every ``SSEReplayBuffer`` promises, run against each implementation.

What one backend adds on top - the in-memory ring's numbering and session
bound, the Redis stream's trimming and expiry - is in that backend's own file.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis, FakeServer

from django_spec_mcp.transport.in_memory_sse_replay_buffer import InMemorySSEReplayBuffer
from django_spec_mcp.transport.redis_sse_replay_buffer import RedisSSEReplayBuffer
from django_spec_mcp.transport.types.sse_replay_buffer import SSEReplayBuffer


@pytest.fixture(params=["in-memory", "redis"])
async def buffer(request: pytest.FixtureRequest) -> AsyncIterator[SSEReplayBuffer]:
    """Each implementation, the Redis one on a ``fakeredis`` server of its own.

    A server per test, because before fakeredis 2.21 a bare client shares one
    process-wide server and keys would leak between tests.
    """
    if request.param == "in-memory":
        yield InMemorySSEReplayBuffer()
        return
    client = FakeAsyncRedis(server=FakeServer())
    yield RedisSSEReplayBuffer(client)
    await client.aclose()


async def _drain(it: AsyncIterator[tuple[str, Any]]) -> list[tuple[str, Any]]:
    return [pair async for pair in it]


def test_satisfies_the_protocol(buffer: SSEReplayBuffer) -> None:
    assert isinstance(buffer, SSEReplayBuffer)


async def test_record_returns_monotonic_ids(buffer: SSEReplayBuffer) -> None:
    a = await buffer.record("s", {"n": 1})
    b = await buffer.record("s", {"n": 2})
    c = await buffer.record("s", {"n": 3})
    # Compared as strings, which is how a resuming client's ``Last-Event-ID``
    # is compared: Redis's ``ms-seq`` ids and the ring's zero-padded counter
    # both sort by recency.
    assert a < b < c


async def test_replay_yields_events_after_id(buffer: SSEReplayBuffer) -> None:
    first = await buffer.record("s", {"n": 1})
    second = await buffer.record("s", {"n": 2})
    third = await buffer.record("s", {"n": 3})
    out = await _drain(buffer.replay("s", first))
    assert out == [(second, {"n": 2}), (third, {"n": 3})]


async def test_replay_after_latest_yields_nothing(buffer: SSEReplayBuffer) -> None:
    """Client already up to date: no events past the head."""
    last = await buffer.record("s", {"n": 1})
    assert await _drain(buffer.replay("s", last)) == []


async def test_replay_with_none_yields_nothing(buffer: SSEReplayBuffer) -> None:
    await buffer.record("s", {"n": 1})
    assert await _drain(buffer.replay("s", None)) == []


async def test_replay_unknown_session_yields_nothing(buffer: SSEReplayBuffer) -> None:
    assert await _drain(buffer.replay("never-existed", "0-0")) == []


async def test_buckets_are_per_session(buffer: SSEReplayBuffer) -> None:
    a0 = await buffer.record("a", {"src": "a", "n": 0})
    b0 = await buffer.record("b", {"src": "b", "n": 0})
    a1 = await buffer.record("a", {"src": "a", "n": 1})
    b1 = await buffer.record("b", {"src": "b", "n": 1})
    assert await _drain(buffer.replay("a", a0)) == [(a1, {"src": "a", "n": 1})]
    assert await _drain(buffer.replay("b", b0)) == [(b1, {"src": "b", "n": 1})]


async def test_forget_drops_session_state(buffer: SSEReplayBuffer) -> None:
    first = await buffer.record("s", {"n": 1})
    await buffer.record("s", {"n": 2})
    await buffer.forget("s")
    assert await _drain(buffer.replay("s", first)) == []


async def test_forget_unknown_session_is_noop(buffer: SSEReplayBuffer) -> None:
    await buffer.forget("never-existed")
    assert await _drain(buffer.replay("never-existed", "0-0")) == []
