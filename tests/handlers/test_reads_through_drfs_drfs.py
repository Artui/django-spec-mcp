"""Which resources that are not the kernel's read through ``read_drfs_resource``.

``reads_through_drfs`` sends a binding down the drf-services read when it holds
anything that path builds for: an output serializer, a ``kwargs`` provider, a
``DRFPermissionAdapter`` or a selector taking from the pool. Each condition is
one conjunct of a single arc, so each is held by its own test here, written so
the plain path would answer it differently. A view holding none of them reads
plainly, which ``test_ui_resources.py`` asks.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django_service_specs import SelectorKind
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.permissions.drf_permission_adapter import DRFPermissionAdapter
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.resource_encoding import ResourceEncoding


class _Label(serializers.Serializer):
    label = serializers.CharField()


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _registered(**overrides: Any) -> MCPServer:
    server = _server()
    defaults: dict[str, Any] = {
        "name": "thing",
        "uri_template": "things://x",
        "description": None,
        "selector": lambda: {"label": "one", "secret": "kept out"},
        "kind": SelectorKind.RETRIEVE,
        "permissions": (),
    }
    server.resources.register(ResourceBinding(**{**defaults, **overrides}))
    return server


async def _read(server: MCPServer, uri: str, *, is_async: bool) -> Any:
    context = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )
    if is_async:
        return await handle_resources_read_async({"uri": uri}, context)
    return await sync_to_async(handle_resources_read)({"uri": uri}, context)


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def is_async(request: pytest.FixtureRequest) -> bool:
    return request.param


async def test_a_resource_with_an_output_serializer_reads_through_drfs(is_async: bool) -> None:
    # Read plainly, the selector's whole return value would be encoded.
    out = await _read(_registered(output_serializer=_Label), "things://x", is_async=is_async)

    assert out["contents"][0]["text"] == '{\n  "label": "one"\n}'


async def test_a_resource_with_a_kwargs_provider_reads_through_drfs(is_async: bool) -> None:
    # Read plainly, the provider would never be called.
    seen: list[Any] = []

    def provider(view: Any, request: Any) -> dict[str, Any]:
        seen.append((view.action, type(request)))
        return {}

    await _read(_registered(kwargs_provider=provider), "things://x", is_async=is_async)

    assert seen == [("thing", Request)]


async def test_a_view_guarded_by_a_drf_permission_reads_through_drfs(is_async: bool) -> None:
    # Read plainly, the class would be judged once, on an empty route, where
    # the drf-services read judges it on the read's view and again at the guard.
    seen: list[Any] = []

    class _Recording(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            seen.append(view.action)
            return True

    server = _server()
    server.register_ui_resource(
        name="board",
        uri="ui://board.html",
        html="<p>board</p>",
        permissions=[DRFPermissionAdapter(_Recording)],
    )

    out = await _read(server, "ui://board.html", is_async=is_async)

    assert out["contents"][0]["text"] == "<p>board</p>"
    assert seen == ["board", "board"]


def _by_name(request: Any) -> str:
    return type(request).__name__


def _by_keyword(*, request: Any) -> str:
    return type(request).__name__


def _by_catch_all(**pool: Any) -> str:
    return type(pool["request"]).__name__


@pytest.mark.parametrize("selector", [_by_name, _by_keyword, _by_catch_all])
async def test_a_resource_whose_selector_takes_the_pool_reads_through_drfs(
    is_async: bool, selector: Any
) -> None:
    # Read plainly, ``request`` would be the transport's ``HttpRequest``; each
    # parameter kind the pool fills is one member of ``_FILLABLE``.
    server = _registered(selector=selector, encoding=ResourceEncoding.TEXT)

    out = await _read(server, "things://x", is_async=is_async)

    assert out["contents"][0]["text"] == "Request"


async def test_a_resource_holding_none_of_them_reads_plainly(is_async: bool) -> None:
    out = await _read(_registered(), "things://x", is_async=is_async)

    assert out["contents"][0]["text"] == '{\n  "label": "one",\n  "secret": "kept out"\n}'


async def test_the_serializer_runs_before_the_encoding_check(is_async: bool) -> None:
    """Order matters: the encoding check must see the *rendered* value, so a
    serializer can't turn a TEXT binding's payload into a body by accident
    and go unreported."""
    server = _registered(output_serializer=_Label, encoding=ResourceEncoding.TEXT)

    out = await _read(server, "things://x", is_async=is_async)

    # ``ReturnDict``, not ``dict`` — the rendered type, not the raw one.
    assert "ReturnDict" in out.message
