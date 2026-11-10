from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from django.http import StreamingHttpResponse

from django_spec_mcp.transport.types.sse_broker import SSEBroker
from django_spec_mcp.transport.types.sse_replay_buffer import SSEReplayBuffer

# Time between idle keep-alive comments. The MCP spec prescribes no value;
# 15 s avoids most intermediate proxy timeouts (60 s+ idle is risky behind
# nginx / cloudflare) without flooding the connection.
_KEEPALIVE_INTERVAL_SECONDS: float = 15.0


def keepalive_interval_seconds() -> float:
    """The idle keep-alive period shared by both SSE surfaces."""
    return _KEEPALIVE_INTERVAL_SECONDS


def format_event(payload: Any, *, event_id: str | None = None) -> bytes:
    """Encode a single JSON-RPC payload as one SSE event."""
    body: str = json.dumps(payload, separators=(",", ":"))
    if event_id is None:
        return f"data: {body}\n\n".encode()
    return f"id: {event_id}\ndata: {body}\n\n".encode()


async def stream_events(
    broker: SSEBroker,
    session_id: str,
    *,
    keepalive_interval: float = _KEEPALIVE_INTERVAL_SECONDS,
    replay_buffer: SSEReplayBuffer | None = None,
    last_event_id: str | None = None,
    max_seconds: float | None = None,
) -> AsyncIterator[bytes]:
    """Async generator that yields SSE bytes for one session's stream."""
    queue: asyncio.Queue[Any] = broker.subscribe(session_id)
    deadline: float | None = (
        None if max_seconds is None else asyncio.get_running_loop().time() + max_seconds
    )
    try:
        yield b": stream open\n\n"
        if replay_buffer is not None and last_event_id is not None:
            async for event_id, payload in replay_buffer.replay(  # pragma: no branch
                session_id, last_event_id
            ):
                yield format_event(payload, event_id=event_id)
        while True:
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                yield b": stream closed\n\n"
                return
            try:
                payload: Any = await asyncio.wait_for(queue.get(), timeout=keepalive_interval)
            except asyncio.TimeoutError:
                yield b": keepalive\n\n"
                continue
            if (
                replay_buffer is not None
                and isinstance(payload, dict)
                and "_mcp_event_id" in payload
                and "_mcp_payload" in payload
            ):
                yield format_event(payload["_mcp_payload"], event_id=payload["_mcp_event_id"])
            else:
                yield format_event(payload)
    finally:
        broker.unsubscribe(session_id, queue)


def build_sse_response(
    broker: SSEBroker,
    session_id: str,
    *,
    keepalive_interval: float = _KEEPALIVE_INTERVAL_SECONDS,
    replay_buffer: SSEReplayBuffer | None = None,
    last_event_id: str | None = None,
    max_seconds: float | None = None,
) -> StreamingHttpResponse:
    """Build the spec-compliant ``StreamingHttpResponse`` for an SSE GET."""
    response = StreamingHttpResponse(
        stream_events(
            broker,
            session_id,
            keepalive_interval=keepalive_interval,
            replay_buffer=replay_buffer,
            last_event_id=last_event_id,
            max_seconds=max_seconds,
        ),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


__all__ = ["build_sse_response", "format_event", "keepalive_interval_seconds", "stream_events"]
