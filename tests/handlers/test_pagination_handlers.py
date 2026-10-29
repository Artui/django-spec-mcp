"""Pagination wired through tools/list, resources/list, resources/templates/list, on kernel specs.

Pages and cursor refusals, over tools and resources registered with
``register_tool`` and ``register_resource``. A kernel resource reads one row, so
the concrete resources here are ``RETRIEVE`` specs with no variables; the
listing pages over bindings, not over what they read.
"""

from __future__ import annotations

from typing import Any

import pytest
from django_service_specs import (
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.handlers.handle_resources_list import handle_resources_list
from django_spec_mcp.handlers.handle_resources_templates_list import (
    handle_resources_templates_list,
)
from django_spec_mcp.handlers.handle_tools_list import handle_tools_list
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.utils import call_context


def _row(pk: int) -> dict[str, int]:
    return {"pk": pk}


def _build_server() -> MCPServer:
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    for i in range(5):
        server.register_tool(
            ServiceSpec(service=lambda: None, permissions=[Unrestricted()]), name=f"tool.{i}"
        )
    for i in range(4):
        server.register_resource(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=lambda: {}, permissions=[Unrestricted()]
            ),
            name=f"r.{i}",
            uri_template=f"r{i}://",
        )
    for i in range(3):
        server.register_resource(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_row,
                reads=Parameters([Parameter("pk", "integer", required=True)]),
                permissions=[Unrestricted()],
            ),
            name=f"rt.{i}",
            uri_template=f"rt{i}://" + "{pk}",
        )
    return server


_LISTINGS = pytest.mark.parametrize(
    ("handler", "key"),
    [
        (handle_tools_list, "tools"),
        (handle_resources_list, "resources"),
        (handle_resources_templates_list, "resourceTemplates"),
    ],
    ids=["tools", "resources", "templates"],
)


def test_tools_list_paginates(settings) -> None:
    settings.SPEC_MCP = {"PAGE_SIZE": 2}
    server = _build_server()
    out1 = handle_tools_list(None, call_context(server))
    assert isinstance(out1, dict)
    assert len(out1["tools"]) == 2
    assert "nextCursor" in out1
    out2 = handle_tools_list({"cursor": out1["nextCursor"]}, call_context(server))
    assert isinstance(out2, dict)
    assert len(out2["tools"]) == 2
    assert "nextCursor" in out2
    out3 = handle_tools_list({"cursor": out2["nextCursor"]}, call_context(server))
    assert isinstance(out3, dict)
    assert len(out3["tools"]) == 1
    assert "nextCursor" not in out3


@pytest.mark.parametrize(
    ("handler", "key"),
    [(handle_resources_list, "resources"), (handle_resources_templates_list, "resourceTemplates")],
    ids=["resources", "templates"],
)
def test_a_resource_listing_paginates(settings, handler: Any, key: str) -> None:
    settings.SPEC_MCP = {"PAGE_SIZE": 2}
    out = handler(None, call_context(_build_server()))
    assert isinstance(out, dict)
    assert len(out[key]) == 2
    assert "nextCursor" in out


@_LISTINGS
@pytest.mark.parametrize("cursor", [42, "###"], ids=["non-string", "malformed"])
def test_a_listing_rejects_a_bad_cursor(handler: Any, key: str, cursor: Any) -> None:
    out = handler({"cursor": cursor}, call_context(_build_server()))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602
