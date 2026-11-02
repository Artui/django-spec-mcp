"""A ``subscriptions/listen`` refused at the concurrency cap echoes the request's ``id``.

The ``503`` is a JSON-RPC error answering one request; a client matching by
``id`` cannot place it if the ``id`` is null. Subscriptions are an async-view
feature, so there is no sync twin.
"""

from __future__ import annotations

import json

from django.test import AsyncClient, override_settings

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.subscriptions.in_memory_subscription_broker import InMemorySubscriptionBroker
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.urlconf_for import urlconf_for

_MODERN_VERSION: str = "2026-07-28"


async def test_the_capacity_503_carries_the_requests_id() -> None:
    server = MCPServer(
        name="capped",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        subscription_broker=InMemorySubscriptionBroker(),
        config=build_mcp_config(max_concurrent_subscriptions=0),
    )
    body = {
        "jsonrpc": "2.0",
        "id": 4242,
        "method": "subscriptions/listen",
        "params": {
            "notifications": {},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": _MODERN_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
        response = await AsyncClient().post(
            "/mcp/",
            data=json.dumps(body),
            content_type="application/json",
            headers={"Mcp-Method": "subscriptions/listen", "Mcp-Protocol-Version": _MODERN_VERSION},
        )
    assert response.status_code == 503, response.content
    assert json.loads(response.content)["id"] == 4242
