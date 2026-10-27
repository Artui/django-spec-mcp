"""The handlers' guard paths, called directly against the kernel-twin server's registries.

Asks of ``build_kernel_server`` what the original, kept beside this file as
``test_handler_error_paths_drfs.py``, asks of the drf-services test app's
server: each handler names a malformed request as a ``-32602`` (or an unknown
URI as ``-32002``) before any spec is reached, and a kernel service raising
``ServiceError`` is a tool-level ``isError`` result rather than a JSON-RPC error.
The concrete resource listed is a kernel ``RETRIEVE`` with no URI variable, since
the kernel twins have no list resource.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django_service_specs import SelectorKind, SelectorSpec, ServiceError, ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_initialize import handle_initialize
from django_spec_mcp.handlers.handle_ping import handle_ping
from django_spec_mcp.handlers.handle_resources_list import handle_resources_list
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from tests.kernel_client import KernelClient
from tests.testapp.mcp import build_kernel_server
from tests.testapp.urlconf_for import urlconf_for

OPEN = [Unrestricted()]


def _boom() -> None:
    raise ServiceError("nope")


def _server() -> MCPServer:
    server = build_kernel_server()
    server.register_resource(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: {"ok": True}, permissions=OPEN),
        name="ledger",
        uri_template="ledger://",
    )
    server.register_tool(ServiceSpec(service=_boom, permissions=OPEN), name="boom.raise")
    return server


def _ctx() -> MCPCallContext:
    server = _server()
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def test_handle_initialize_rejects_non_dict_params() -> None:
    out = handle_initialize(None, _ctx())
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602


def test_handle_initialize_negotiates_an_unknown_version() -> None:
    out = handle_initialize(
        {
            "protocolVersion": "1999-01-01",
            "capabilities": {},
            "clientInfo": {"name": "x", "version": "1"},
        },
        _ctx(),
    )
    # Unsupported version: the server falls back to its preferred version.
    assert not isinstance(out, JsonRpcError)
    assert out.protocol_version == "2025-11-25"


def test_handle_ping_returns_an_empty_object() -> None:
    assert handle_ping(None, _ctx()) == {}


@pytest.mark.parametrize(
    "params",
    [None, {}, {"name": "invoices.create", "arguments": [1, 2, 3]}],
    ids=["non-dict-params", "missing-name", "non-dict-arguments"],
)
def test_handle_tools_call_rejects_a_malformed_call_on_a_kernel_tool(params: Any) -> None:
    out = handle_tools_call(params, _ctx())
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602


@pytest.mark.parametrize(
    ("params", "code"),
    [(None, -32602), ({}, -32602), ({"uri": "nope://x"}, -32002)],
    ids=["non-dict-params", "missing-uri", "unknown-uri"],
)
def test_handle_resources_read_rejects_a_malformed_or_unknown_read(params: Any, code: int) -> None:
    out = handle_resources_read(params, _ctx())
    assert isinstance(out, JsonRpcError)
    assert out.code == code


def test_handle_resources_list_returns_a_concrete_kernel_resource() -> None:
    out = handle_resources_list({}, _ctx())
    assert isinstance(out, dict)
    assert "ledger://" in [r["uri"] for r in out["resources"]]


@pytest.mark.django_db(transaction=True)
def test_a_kernel_service_error_is_a_tool_level_result(client: Client) -> None:
    """Business failures are tool-level: the JSON-RPC envelope succeeds and the
    result carries ``isError: true``."""
    with override_settings(ROOT_URLCONF=urlconf_for(_server())):
        caller = KernelClient(client)
        caller.initialize()
        body = caller.call("boom.raise")
    assert "error" not in body
    assert body["result"]["isError"] is True
