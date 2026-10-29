"""A kernel tool's argument refusal on ``tools/call`` is a tool execution error, not ``-32602``.

The MCP spec's tools "Error Handling" section lists "Input validation errors
(e.g., date in wrong format, value out of range)" under *tool execution
errors*, reported in the result with ``isError: true`` so a model can read them
and correct itself (2025-11-25, 2026-07-28). Protocol errors are kept for an
unknown tool and a request that fails the ``CallToolRequest`` schema itself.

On the kernel path every refusal of the arguments is the kernel's own
``InvalidArguments``: an unexpected argument under ``UnknownArguments.REJECT``,
a Validator rejecting a field, and a read whose value its declared type
refuses. Each is served as the ``validation_error`` result with the refusal's
per-field ``detail`` and the message ``"Invalid arguments"``, on both tool
kinds, every entry point and both protocol eras. A kernel selector's filters are
declared ``reads``, so a refused filter value is the refused read. The
unexpected argument's answer is keyed by its own name, ``Unknown argument.``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django.test import Client
from django_service_specs import (
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from tests.conftest import post_jsonrpc
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import tool_error

pytestmark = pytest.mark.django_db(transaction=True)

MODERN = "2026-07-28"
OPEN = [Unrestricted()]


@dataclass
class _Charge:
    number: str
    amount_cents: int = 0


def _charge(*, number: str, amount_cents: int = 0) -> dict[str, Any]:
    return {"number": number, "amount_cents": amount_cents}


def _read(*, pk: int) -> dict[str, int]:
    return {"pk": pk}


def _server(**kwargs: Any) -> MCPServer:
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore(), **kwargs
    )
    server.register_tool(
        ServiceSpec(service=_charge, validator=DataclassValidator(_Charge), permissions=OPEN),
        name="service",
    )
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_read,
            reads=Parameters([Parameter("pk", "integer", required=True)]),
            permissions=OPEN,
        ),
        name="selector",
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
        config=server.config,
    )


_ROUTES = ["handler", "async_handler", "call_tool", "acall_tool"]


async def _via(server: MCPServer, route: str, name: str, arguments: Any) -> Any:
    """The result payload of ``name`` called with ``arguments`` through ``route``."""
    if route == "call_tool":
        return (await sync_to_async(server.call_tool)(name, arguments, user=None)).to_dict()
    if route == "acall_tool":
        return await server.acall_tool(name, arguments, user=None)
    params: dict[str, Any] = {"name": name, "arguments": arguments}
    if route == "async_handler":
        return await handle_tools_call_async(params, _ctx(server))
    return await sync_to_async(handle_tools_call)(params, _ctx(server))


def _invalid(out: Any) -> dict[str, Any]:
    """The ``detail`` of a ``validation_error`` result, asserting it is not ``-32602``."""
    assert not isinstance(out, JsonRpcError), f"answered as a protocol error: {out!r}"
    error = tool_error(out)
    assert error["type"] == "validation_error"
    assert error["message"] == "Invalid arguments"
    return error["detail"]


# (tool, arguments, the detail the refusal serves)
_REFUSALS = [
    ("service", {"number": "A", "rogue": 1}, {"rogue": ["Unknown argument."]}),
    ("selector", {"pk": 1, "rogue": 1}, {"rogue": ["Unknown argument."]}),
    (
        "service",
        {"number": "A", "amount_cents": "lots"},
        {"amount_cents": ["Expected an integer."]},
    ),
    ("selector", {"pk": "first"}, {"pk": ["Expected an integer."]}),
]
_REFUSAL_IDS = ["service-unexpected", "selector-unexpected", "validator", "read"]


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize(("name", "arguments", "detail"), _REFUSALS, ids=_REFUSAL_IDS)
async def test_a_refused_argument_is_a_validation_error_result(
    route: str, name: str, arguments: dict[str, Any], detail: dict[str, Any]
) -> None:
    out = await _via(_server(), route, name, arguments)

    assert _invalid(out) == detail


@pytest.mark.parametrize("route", _ROUTES)
async def test_the_arguments_are_echoed_only_when_configured(route: str) -> None:
    """``INCLUDE_VALIDATION_VALUE`` governs the kernel path's result as it does the other's."""
    arguments = {"number": "A", "amount_cents": "lots"}

    quiet = tool_error(await _via(_server(), route, "service", arguments))
    echoed = tool_error(
        await _via(
            _server(config=build_mcp_config(include_validation_value=True)),
            route,
            "service",
            arguments,
        )
    )

    assert "value" not in quiet
    assert echoed["value"] == arguments


# ---------- what stays a protocol error ----------


@pytest.mark.parametrize("route", ["handler", "async_handler", "acall_tool"])
async def test_an_unknown_tool_is_still_minus_32602(route: str) -> None:
    out = await _via(_server(), route, "nope", {})

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.INVALID_PARAMS


# Not ``acall_tool``, which reads a falsy ``arguments`` as none sent.
@pytest.mark.parametrize("route", ["handler", "async_handler"])
@pytest.mark.parametrize("arguments", [["number"], "number", 0, False])
async def test_non_object_arguments_are_still_minus_32602(arguments: Any, route: str) -> None:
    """A request failing ``CallToolRequest``'s own schema is malformed, not invalid input."""
    out = await _via(_server(), route, "service", arguments)

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.INVALID_PARAMS
    assert out.message == "'arguments' must be an object"


# ---------- the wire, in both protocol eras ----------


@pytest.fixture
def mounted(settings: Any) -> None:
    """Mount this file's server; listed before ``era_session`` so ``initialize`` reaches it."""
    settings.ROOT_URLCONF = urlconf_for(_server())


@pytest.fixture(params=["2025-11-25", MODERN])
def era_session(mounted: None, request: Any, client: Client) -> str | None:
    if request.param == MODERN:
        return None
    return request.getfixturevalue("initialized_session")


def _post(client: Client, era_session: str | None, params: dict[str, Any]) -> Any:
    if era_session is not None:
        return post_jsonrpc(client, method="tools/call", params=params, session_id=era_session)
    meta = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0.0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    return client.post(
        "/mcp/",
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {**params, "_meta": meta}}
        ),
        content_type="application/json",
        headers={
            "Mcp-Protocol-Version": MODERN,
            "Mcp-Method": "tools/call",
            "Mcp-Name": params["name"],
        },
    )


@pytest.mark.parametrize(("name", "arguments", "detail"), _REFUSALS, ids=_REFUSAL_IDS)
def test_the_wire_serves_a_refused_argument_as_a_result(
    client: Client,
    era_session: str | None,
    name: str,
    arguments: dict[str, Any],
    detail: dict[str, Any],
) -> None:
    response = _post(client, era_session, {"name": name, "arguments": arguments})

    assert response.status_code == 200, response.content
    body = response.json()
    assert "error" not in body, body
    assert _invalid(body["result"]) == detail


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"name": "nope", "arguments": {}}, "Unknown tool: 'nope'"),
        ({"name": "service", "arguments": ["number"]}, "'arguments' must be an object"),
    ],
)
def test_the_wire_keeps_the_protocol_errors(
    client: Client, era_session: str | None, params: dict[str, Any], message: str
) -> None:
    body = _post(client, era_session, params).json()

    assert "result" not in body, body
    assert body["error"]["code"] == -32602
    assert body["error"]["message"] == message
