from __future__ import annotations

import pytest

from django_spec_mcp.transport.in_memory_sse_replay_buffer import InMemorySSEReplayBuffer


async def _drain(it) -> list[tuple[str, object]]:
    return [pair async for pair in it]


async def test_eviction_at_max_events() -> None:
    """Old events drop off the back when the ring fills."""
    buf = InMemorySSEReplayBuffer(max_events=2)
    first = await buf.record("s", {"n": 1})
    await buf.record("s", {"n": 2})
    third = await buf.record("s", {"n": 3})
    out = await _drain(buf.replay("s", "0000000000000000"))
    assert [pair[1] for pair in out] == [{"n": 2}, {"n": 3}]
    out2 = await _drain(buf.replay("s", first))
    assert [pair[1] for pair in out2] == [{"n": 2}, {"n": 3}]
    assert await _drain(buf.replay("s", third)) == []


async def test_each_session_numbers_its_own_events() -> None:
    """IDs are session-scoped and start at 1 for each."""
    buf = InMemorySSEReplayBuffer()
    a1 = await buf.record("a", {"src": "a"})
    b1 = await buf.record("b", {"src": "b"})
    assert a1 == b1 == "0000000000000001"


async def test_forget_restarts_the_sessions_numbering() -> None:
    buf = InMemorySSEReplayBuffer()
    await buf.record("s", {"n": 1})
    await buf.forget("s")
    assert await buf.record("s", {"n": 99}) == "0000000000000001"


def test_invalid_max_events_rejected() -> None:
    with pytest.raises(ValueError, match="max_events"):
        InMemorySSEReplayBuffer(max_events=0)


async def test_sessions_that_never_said_goodbye_are_eventually_forgotten() -> None:
    """``forget`` runs only on an explicit ``DELETE``."""
    buf = InMemorySSEReplayBuffer(max_sessions=2)
    first = await buf.record("s1", {"n": 1})
    await buf.record("s2", {"n": 2})
    await buf.record("s3", {"n": 3})
    assert await _drain(buf.replay("s1", first)) == []
    assert await _drain(buf.replay("s3", "0" * 16)) == [("0" * 15 + "1", {"n": 3})]


async def test_the_session_evicted_is_the_least_recently_written() -> None:
    """Writing marks a session live, so a stream still receiving events is not dropped."""
    buf = InMemorySSEReplayBuffer(max_sessions=2)
    await buf.record("s1", {"n": 1})
    await buf.record("s2", {"n": 2})
    second = await buf.record("s1", {"n": 3})
    await buf.record("s3", {"n": 4})
    assert await _drain(buf.replay("s2", "0" * 16)) == []
    assert second == "0" * 15 + "2"
    assert await _drain(buf.replay("s1", "0" * 16)) == [
        ("0" * 15 + "1", {"n": 1}),
        ("0" * 15 + "2", {"n": 3}),
    ]


async def test_an_unusable_session_bound_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="max_sessions must be positive"):
        InMemorySSEReplayBuffer(max_sessions=0)
