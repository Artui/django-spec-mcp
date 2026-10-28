"""A value a kernel spec computes by a query for a read the call left out is computed off the loop.

What fills a read the call leaves out, by a query, is the spec's ``kwargs``
provider. A provider that queries on the event loop raises
``SynchronousOnlyOperation`` where the sync path serves, so this asks that the
provider is called off the loop on every route a kernel tool is called by: the
sync handler, the async handler and ``acall_tool``.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django_service_specs import (
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from tests.testapp.models import Invoice


def _default_project() -> dict[str, Any]:
    # Any ORM read; the test database holds no invoice, so this is 7.
    return {"project_pk": Invoice.objects.count() + 7}


def _server() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, project_pk: {"project": project_pk},
            reads=Parameters([Parameter("project_pk", "integer")]),
            kwargs=_default_project,
            permissions=[Unrestricted()],
        ),
        name="tool",
        description="Read a project.",
    )
    return server


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        conventions=server.conventions,
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", ["handler", "async_handler", "acall_tool"])
async def test_a_provider_that_queries_fills_the_read_on_every_route(route: str) -> None:
    server = _server()
    params: dict[str, Any] = {"name": "tool", "arguments": {}}
    if route == "acall_tool":
        out: Any = await server.acall_tool("tool", {}, user=None)
    elif route == "async_handler":
        out = await handle_tools_call_async(params, _ctx(server))
    else:
        out = await sync_to_async(handle_tools_call)(params, _ctx(server))
    assert isinstance(out, dict), f"answered {out!r}"
    assert out.get("structuredContent") == {"project": 7}
