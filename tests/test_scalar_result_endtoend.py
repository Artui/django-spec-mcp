"""A tool whose result is a scalar carries no ``structuredContent``.

MCP 2025-11-25 declares ``structuredContent?: { [key: string]: unknown }``, and
makes it optional when the tool declares no ``outputSchema``. A spec with no
presenter whose service returns a bool, a number or a string advertises no
schema, so its result is the text block alone, carrying the scalar as JSON,
rather than the bare scalar as structured content a strict client rejects.

Each entry point is driven, because each answers on its own path: the sync wire
handler, the in-process ``call_tool`` and ``acall_tool``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.http import HttpRequest
from django_service_specs import ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_list import handle_tools_list
from django_spec_mcp.handlers.types.context import MCPCallContext

_SCALARS: dict[str, Any] = {"flag": True, "count": 7, "ratio": 0.5, "label": "done"}


def _server() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    for name, value in _SCALARS.items():
        server.register_tool(
            ServiceSpec(service=lambda value=value: value, permissions=[Unrestricted()]),
            name=name,
            description="d",
        )
    return server


def _ctx(server: MCPServer) -> MCPCallContext:
    request = HttpRequest()
    request.user = None
    return MCPCallContext(
        http_request=request,
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def _wire(server: MCPServer, name: str) -> dict[str, Any]:
    out: Any = handle_tools_call({"name": name, "arguments": {}}, _ctx(server))
    return out


def _in_process(server: MCPServer, name: str) -> dict[str, Any]:
    return server.call_tool(name, {}, user=None).to_dict()


def _async_wire(server: MCPServer, name: str) -> dict[str, Any]:
    out: Any = async_to_sync(server.acall_tool)(name, {}, user=None)
    return out


@pytest.mark.parametrize(
    "call", [_wire, _in_process, _async_wire], ids=["wire", "call_tool", "acall_tool"]
)
@pytest.mark.parametrize("name", list(_SCALARS))
@pytest.mark.django_db(transaction=True)
def test_a_scalar_result_is_the_text_block_alone(call: Any, name: str) -> None:
    server = _server()
    listed: Any = handle_tools_list(None, _ctx(server))
    tool = next(entry for entry in listed["tools"] if entry["name"] == name)

    result = call(server, name)

    assert not result.get("isError"), result
    assert "outputSchema" not in tool
    assert "structuredContent" not in result
    assert json.loads(result["content"][0]["text"]) == _SCALARS[name]
