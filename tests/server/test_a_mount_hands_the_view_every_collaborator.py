"""Both mounts hand the endpoint view every collaborator the server holds.

A collaborator left out of ``as_view`` fails with no error on either side: the
view falls back to its class default, ``None``. A replay buffer dropped from
``async_urls`` means every ``Last-Event-ID`` is silently ignored, and nothing
else in the suite reaches the wiring, so it is asserted here, by identity.
"""

from __future__ import annotations

from typing import Any

import pytest

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.transport.in_memory_sse_broker import InMemorySSEBroker
from django_spec_mcp.transport.in_memory_sse_replay_buffer import InMemorySSEReplayBuffer


@pytest.mark.parametrize("is_async", [False, True], ids=["urls", "async_urls"])
def test_a_mount_hands_the_view_every_collaborator(is_async: bool) -> None:
    broker = InMemorySSEBroker()
    replay = InMemorySSEReplayBuffer()
    server = MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        sse_broker=broker,
        sse_replay_buffer=replay,
    )
    patterns, _, _ = server.async_urls if is_async else server.urls
    handed: dict[str, Any] = patterns[0].callback.view_initkwargs

    expected: dict[str, Any] = {
        "tools": server._tools,
        "resources": server._resources,
        "prompts": server._prompts,
        "auth_backend": server._auth_backend,
        "session_store": server._session_store,
        "task_store": server._task_store,
        "task_executor": server._task_executor,
        "subscription_broker": server._subscription_broker,
        "pool_seeds": server._pool_seeds,
        "conventions": server._conventions,
        "server_info": server._server_info,
        "config": server._config,
    }
    if is_async:
        expected |= {"sse_broker": broker, "sse_replay_buffer": replay}
    assert handed.keys() == {*expected, "instructions"}
    for name, value in expected.items():
        assert handed[name] is value, name
    assert handed["instructions"] == server.description
