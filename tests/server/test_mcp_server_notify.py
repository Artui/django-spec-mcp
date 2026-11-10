from __future__ import annotations

from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.utils import make_server


async def test_notify_returns_false_when_no_subscriber() -> None:
    server = make_server(session_store=InMemorySessionStore())
    delivered = await server.notify("missing", {"x": 1})
    assert delivered is False


async def test_notify_returns_true_and_enqueues_for_subscriber() -> None:
    server = make_server(session_store=InMemorySessionStore())
    queue = server.sse_broker.subscribe("s1")
    delivered = await server.notify("s1", {"hello": "world"})
    assert delivered is True
    payload = await queue.get()
    assert payload == {"hello": "world"}


async def test_sse_broker_property_exposes_owned_instance() -> None:
    server = make_server(session_store=InMemorySessionStore())
    assert server.sse_broker is server._sse_broker  # type: ignore[attr-defined]


async def test_notify_records_to_buffer_and_wraps_payload() -> None:
    """When a replay buffer is wired in, ``notify`` records first then publishes wrapped."""
    from django_spec_mcp.transport.in_memory_sse_replay_buffer import (
        InMemorySSEReplayBuffer,
    )

    buffer = InMemorySSEReplayBuffer()
    server = make_server(session_store=InMemorySessionStore(), sse_replay_buffer=buffer)
    queue = server.sse_broker.subscribe("s1")
    delivered = await server.notify("s1", {"hello": "world"})
    assert delivered is True
    payload = await queue.get()
    assert payload == {
        "_mcp_event_id": "0000000000000001",
        "_mcp_payload": {"hello": "world"},
    }
    out: list[tuple[str, object]] = []
    async for pair in buffer.replay("s1", "0000000000000000"):
        out.append(pair)
    assert out == [("0000000000000001", {"hello": "world"})]


def test_sse_replay_buffer_property_returns_configured_buffer() -> None:
    from django_spec_mcp.transport.in_memory_sse_replay_buffer import (
        InMemorySSEReplayBuffer,
    )

    buffer = InMemorySSEReplayBuffer()
    server = make_server(session_store=InMemorySessionStore(), sse_replay_buffer=buffer)
    assert server.sse_replay_buffer is buffer


def test_sse_replay_buffer_property_defaults_to_none() -> None:
    server = make_server(session_store=InMemorySessionStore())
    assert server.sse_replay_buffer is None
