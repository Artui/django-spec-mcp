"""A capability is a promise, and two of them can only be kept for one era.

Every push flag — the three ``listChanged`` fields and ``subscribe`` — describes
a notification that leaves through ``subscriptions/listen``, which is a
modern-only method. ``extensions`` is not even a field on the legacy
``ServerCapabilities``. Advertising either to a legacy client promises something
nothing on its path can deliver, and a client acting on it does not get a clean
error — for ``listChanged`` it gets silence, which is worse.

These ask it of a server whose resources and tool are registered with
``register_resource`` and ``register_tool`` on kernel specs. The server,
context and executor are local to this file, so it reads no other suite's
fixtures.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django_service_specs import SelectorKind, SelectorSpec, ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_initialize import build_capabilities, handle_initialize
from django_spec_mcp.handlers.handle_server_discover import handle_server_discover
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.tasks_extension_id import TASKS_EXTENSION_ID
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.urlconf_for import urlconf_for

MODERN = "2026-07-28"
LEGACY = "2025-11-25"


class _RecordingExecutor:
    """Records what it was handed instead of queueing it."""

    def __init__(self) -> None:
        self.enqueued: list[str] = []

    def enqueue(self, task_id: str) -> None:
        self.enqueued.append(task_id)


def _thing() -> dict[str, str]:
    return {"thing": "yes"}


def _slow() -> dict[str, str]:
    return {"done": "yes"}


def _server(**kwargs: Any) -> MCPServer:
    server = MCPServer(
        name="subs",
        auth_backend=HeaderAuthBackend(),
        session_store=InMemorySessionStore(),
        **kwargs,
    )
    server.register_resource(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_thing, permissions=[Unrestricted()]),
        name="open",
        uri_template="open://thing",
    )
    return server


def _context(server: MCPServer, **overrides: Any) -> MCPCallContext:
    request = HttpRequest()
    request.method = "POST"
    base: dict[str, Any] = {
        "http_request": request,
        "token": TokenInfo(user=None),
        "tools": server.tools,
        "resources": server.resources,
        "prompts": server.prompts,
        "protocol_version": MODERN,
        "client_capabilities": {"extensions": {TASKS_EXTENSION_ID: {}}},
        "tasks": server.task_store,
        "subscriptions": server.subscription_broker,
        "config": server.config,
    }
    base.update(overrides)
    return MCPCallContext(**base)


def _pushing_server() -> MCPServer:
    """A server with everything behind every flag — so any suppression below is
    the era's doing and not a missing collaborator."""
    return _server(subscription_broker=InMemorySubscriptionBroker())


def _full_server() -> MCPServer:
    store = InMemoryTaskStore()
    server = _server(
        subscription_broker=InMemorySubscriptionBroker(),
        task_store=store,
        task_executor=_RecordingExecutor(),
    )
    server.register_tool(
        ServiceSpec(service=_slow, permissions=[Unrestricted()]), name="x", description="x"
    )
    return server


def _full_context(server: MCPServer, **overrides: Any) -> Any:
    """``_context`` does not thread the executor, and the tasks capability needs
    both halves — a store without one creates tasks nothing will ever run."""
    return _context(server, task_executor=server.task_executor, **overrides)


# ---------- the bundle, per era ----------


def test_a_modern_caller_is_told_about_the_pushes() -> None:
    caps = build_capabilities(_context(_pushing_server()), modern=True)
    assert caps.resources == {"listChanged": True, "subscribe": True}
    assert caps.prompts is None or caps.prompts == {"listChanged": True}


def test_a_legacy_caller_is_not() -> None:
    """Same server, same broker — the difference is only
    who is asking, and a legacy client has no method to receive any of it.
    Suppressing the flags must not suppress the capability: a legacy client
    still has resources to read, and ``{}`` means supported."""
    caps = build_capabilities(_context(_pushing_server()), modern=False)
    assert caps.resources == {}


@pytest.mark.parametrize("modern", [False, True], ids=["legacy", "modern"])
def test_extensions_are_offered_to_a_modern_caller_alone(modern: bool) -> None:
    """Not a field on the legacy ``ServerCapabilities`` at all — extension
    negotiation arrived with ``2026-07-28`` — and the tasks path is unreachable
    for a legacy client anyway, since the extension must be declared per
    request."""
    caps = build_capabilities(_full_context(_full_server()), modern=modern)

    assert (caps.extensions is not None) is modern


# ---------- how each handler decides ----------


def test_initialize_never_advertises_a_push_whatever_the_context_says() -> None:
    """``initialize`` does not exist in ``2026-07-28``, so reaching the handler
    *is* the proof of era. Pinned with a context claiming a modern version, which
    is exactly the state that would make a version-sniffing implementation get it
    wrong."""
    result = handle_initialize(
        {"protocolVersion": LEGACY, "capabilities": {}, "clientInfo": {"name": "t"}},
        _full_context(_full_server(), protocol_version=MODERN),
    )
    assert result.capabilities.resources == {}
    assert result.capabilities.extensions is None


def test_discover_follows_the_caller_not_the_server() -> None:
    """The versions and the identity are properties of the endpoint; these two
    capabilities are properties of the caller's reach."""
    server = _full_server()
    modern = handle_server_discover(None, _full_context(server, protocol_version=MODERN))
    legacy = handle_server_discover(None, _full_context(server, protocol_version=LEGACY))
    assert modern["capabilities"]["resources"] == {"listChanged": True, "subscribe": True}
    assert legacy["capabilities"]["resources"] == {}
    assert "extensions" not in legacy["capabilities"]


# ---------- what a legacy client would have done with the promise ----------


@pytest.mark.django_db(transaction=True)
def test_the_method_a_legacy_client_would_have_called_does_not_exist(client: Client) -> None:
    """``resources/subscribe`` is deliberately not implemented: optional in
    ``2025-11-25``, and in ``2026-07-28`` the schema says
    ``SubscriptionFilter.resourceUris`` *"replaces the former resources/subscribe
    RPC"*. This is the concrete thing the old advertisement invited a client to
    do — so the fix is the advertisement, not the method."""
    with override_settings(ROOT_URLCONF=urlconf_for(_pushing_server())):
        handshake = client.post(
            "/mcp/",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": LEGACY,
                        "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"},
                    },
                }
            ),
            content_type="application/json",
        )
        capabilities: dict[str, Any] = json.loads(handshake.content)["result"]["capabilities"]
        assert "subscribe" not in capabilities.get("resources", {})

        attempt = client.post(
            "/mcp/",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "resources/subscribe",
                    "params": {"uri": "open://thing"},
                }
            ),
            content_type="application/json",
            headers={
                "Mcp-Session-Id": handshake["Mcp-Session-Id"],
                "Mcp-Protocol-Version": LEGACY,
            },
        )
        assert json.loads(attempt.content)["error"]["code"] == -32601
