from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest
from django_service_specs import PermissionCheck

from django_spec_mcp import JsonRpcError, JsonRpcErrorCode, MCPServer, UIPermission, UIResourceMeta
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.utils import make_server

URI = "ui://invoices/table.html"


class _Deny(PermissionCheck):
    def has_permission(self, *args: Any, **kwargs: Any) -> bool:
        return False


class _Spent:
    def __init__(self) -> None:
        self.asked = 0

    def consume(self, request: HttpRequest, token: TokenInfo) -> int | None:
        self.asked += 1
        return 9


def _server(**view: Any) -> MCPServer:
    server = make_server(session_store=InMemorySessionStore())
    server.register_ui_resource(name="table", uri=URI, html="<p>rows</p>", **view)
    return server


@pytest.mark.django_db(transaction=True)
async def test_aread_resource_answers_the_wire_payload_with_the_views_meta() -> None:
    server = _server(ui=UIResourceMeta(permissions=(UIPermission.CAMERA,)))
    out = await server.aread_resource(URI, user=None)
    assert isinstance(out, dict)
    (entry,) = out["contents"]
    assert entry["uri"] == URI
    assert "<p>rows</p>" in entry["text"]
    assert entry["_meta"]["ui"]["permissions"] == {"camera": {}}


@pytest.mark.django_db(transaction=True)
async def test_aread_resource_unknown_uri_is_a_jsonrpc_error() -> None:
    out = await _server().aread_resource("ui://nope.html", user=None)
    assert isinstance(out, JsonRpcError)
    assert out.data == {"uri": "ui://nope.html"}


@pytest.mark.django_db(transaction=True)
async def test_aread_resource_enforces_the_resources_permissions() -> None:
    out = await _server(permissions=[_Deny()]).aread_resource(URI, user=None)
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.django_db(transaction=True)
async def test_aread_resource_scopes_open_a_scope_gated_view() -> None:
    server = _server(scopes=("views:read",))
    denied = await server.aread_resource(URI, user=None)
    assert isinstance(denied, JsonRpcError)
    assert denied.code == JsonRpcErrorCode.FORBIDDEN
    allowed = await server.aread_resource(URI, user=None, scopes=["views:read"])
    assert isinstance(allowed, dict)


@pytest.mark.django_db(transaction=True)
async def test_aread_resource_past_its_rate_limit_is_refused() -> None:
    limiter = _Spent()
    out = await _server(rate_limits=[limiter]).aread_resource(URI, user=None)
    assert isinstance(out, JsonRpcError)
    assert (out.code, out.data) == (JsonRpcErrorCode.RATE_LIMITED, {"retryAfter": 9})
    assert limiter.asked == 1
