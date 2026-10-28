"""A kernel ``many=True`` service tool: its list travels under ``spec.many_argument``.

MCP ``arguments`` is always a JSON object, so the list a bulk service validates
cannot be the arguments themselves. A kernel ``ServiceSpec`` declaring
``many=True`` names the one argument carrying it, ``items`` unless
``many_argument`` says otherwise, and ``dispatch`` reads the list out of it,
refuses anything sent beside it under ``REJECT``, and keys every refusal under
it with item refusals at their indexes. These tests hold each route to that,
the listing to advertising what dispatch enforces, and the wire to carrying
the index a client needs to find the item it sent.

A kernel spec's arguments are its parameters, and its ``unknown_arguments``
decides what sits beside the list: an argument beside it is refused under
``REJECT`` and dropped under ``IGNORE``, and the top level of the schema is
closed exactly when it is refused.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import jsonschema
import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django.test import Client, override_settings
from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    ServiceSpec,
    ServiceValidationError,
    UnknownArguments,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.testing import assert_tool_result_conforms
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import tool_error

pytestmark = pytest.mark.django_db(transaction=True)

_ROW = {"number": "A-1", "amount_cents": 100}
_OTHER = {"number": "A-2", "amount_cents": 200}


@dataclass
class _Line:
    number: str
    amount_cents: int

    def __post_init__(self) -> None:
        if self.amount_cents < 0:
            raise ValueError("Ensure this value is greater than or equal to 0.")


def _issue(*, data: list[_Line]) -> list[_Line]:
    return list(data)


def _spec(**overrides: Any) -> ServiceSpec:
    declared: dict[str, Any] = {
        "service": _issue,
        "many": True,
        "validator": DataclassValidator(_Line),
        "presenter": DataclassPresenter(_Line),
        "permissions": [Unrestricted()],
        **overrides,
    }
    return ServiceSpec(**declared)


def _server(spec: ServiceSpec | None = None, **registration: Any) -> MCPServer:
    config = registration.pop("config", None)
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None, config=config)
    server.register_tool(spec or _spec(), name="bulk", **registration)
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


def _schema(server: MCPServer) -> dict[str, Any]:
    listed: Any = server.list_tools(user=None)
    return listed["tools"][0]["inputSchema"]


def _post(server: MCPServer, method: str, params: dict[str, Any]) -> Any:
    """The JSON-RPC envelope as served, so an index is read as the encoder wrote it."""
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    headers = {"Mcp-Protocol-Version": "2026-07-28", "Mcp-Method": method}
    if method == "tools/call":
        headers["Mcp-Name"] = params["name"]
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": meta}}
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        response = Client().post(
            "/mcp/", data=json.dumps(body), content_type="application/json", headers=headers
        )
    return json.loads(response.content)


def _call_over_http(server: MCPServer, arguments: dict[str, Any]) -> Any:
    return _post(server, "tools/call", {"name": "bulk", "arguments": arguments})["result"]


def _resolve(detail: dict[str, Any], sent: dict[str, Any]) -> dict[str, Any]:
    """Walk every item refusal back into the arguments it names, as a client would.

    JSON object keys are strings, so an index arrives as ``"1"`` and a client turns
    it back into the position in the array it sent."""
    return {
        f"{argument}[{index}].{field}": sent[argument][int(index)][field]
        for argument, by_index in detail.items()
        for index, fields in by_index.items()
        for field in fields
    }


# ---------- every route hands the service the list ----------


@pytest.mark.parametrize("route", ["handler", "async_handler", "call_tool"])
async def test_every_route_hands_the_service_the_list(route: str) -> None:
    server = _server()
    arguments = {"items": [_ROW, _OTHER]}
    params = {"name": "bulk", "arguments": arguments}

    if route == "call_tool":
        out = (await sync_to_async(server.call_tool)("bulk", arguments, user=None)).to_dict()
    elif route == "async_handler":
        out = await handle_tools_call_async(params, _ctx(server))
    else:
        out = await sync_to_async(handle_tools_call)(params, _ctx(server))

    assert out["structuredContent"] == [_ROW, _OTHER]


def test_a_declared_argument_name_carries_the_list() -> None:
    server = _server(_spec(many_argument="lines"))

    out = server.call_tool("bulk", {"lines": [_ROW]}, user=None)

    assert set(_schema(server)["properties"]) == {"lines"}
    assert out.structured_content == [_ROW]


# ---------- what dispatch refuses ----------


@pytest.mark.parametrize(
    ("policy", "refused"),
    [(UnknownArguments.REJECT, True), (UnknownArguments.IGNORE, False)],
    ids=["reject", "ignore"],
)
def test_an_argument_beside_the_list_follows_the_unknown_arguments_policy(
    policy: UnknownArguments, refused: bool
) -> None:
    """Refused under ``REJECT`` and dropped under ``IGNORE``, where the service is
    handed the list alone; the schema's top level is closed exactly when it is
    refused."""
    ran: list[Any] = []

    def _record(*, data: list[_Line]) -> list[_Line]:
        ran.append(data)
        return list(data)

    server = _server(_spec(service=_record), unknown_arguments=policy)

    out = server.call_tool("bulk", {"items": [_ROW], "note": "x"}, user=None)

    schema = _schema(server)
    if refused:
        error = tool_error(out.to_dict())
        assert error["type"] == "validation_error"
        assert error["detail"] == {"note": ["Unknown argument."]}
        assert ran == []
        assert schema["additionalProperties"] is False
    else:
        assert not out.is_error, out
        assert out.structured_content == [_ROW]
        assert ran == [[_Line(**_ROW)]]
        assert "additionalProperties" not in schema


def test_a_missing_list_is_refused_under_the_argument() -> None:
    error = tool_error(_server().call_tool("bulk", {}, user=None).to_dict())

    assert error["type"] == "validation_error"
    assert error["detail"] == {"items": ["This field is required."]}


def test_an_invalid_item_is_served_at_its_index() -> None:
    """Read off the HTTP response rather than the handler's return value: the
    detail carries ``int`` keys until the encoder turns them into strings, and the
    echoed value is only useful if that path resolves into it."""
    arguments = {"items": [_ROW, {**_OTHER, "amount_cents": "lots"}]}
    server = _server(config=build_mcp_config(include_validation_value=True))

    error = tool_error(_call_over_http(server, arguments))

    assert error["type"] == "validation_error"
    assert error["detail"] == {"items": {"1": {"amount_cents": ["Expected an integer."]}}}
    assert _resolve(error["detail"], error["value"]) == {"items[1].amount_cents": "lots"}


def _refuse_second(*, data: list[_Line]) -> None:
    # A bulk service naming the row it refuses in the shape dispatch gives item
    # refusals, which is the natural thing for one to raise.
    raise ServiceValidationError({"items": {1: {"number": ["Already issued."]}}})


def test_a_service_refusing_an_item_by_index_is_served_as_a_tool_error() -> None:
    """The ``isError`` text is encoded with sorted keys, which an ``int`` key must
    survive, and the index must resolve into the echoed arguments there too."""
    arguments = {"items": [_ROW, _OTHER]}
    server = _server(
        _spec(service=_refuse_second, presenter=None),
        config=build_mcp_config(include_validation_value=True),
    )

    error = tool_error(_call_over_http(server, arguments))

    assert error["type"] == "validation_error"
    assert error["detail"] == {"items": {"1": {"number": ["Already issued."]}}}
    assert _resolve(error["detail"], error["value"]) == {"items[1].number": "A-2"}


# ---------- the listing advertises what dispatch enforces ----------


@pytest.mark.parametrize("policy", [UnknownArguments.REJECT, UnknownArguments.IGNORE])
def test_each_item_is_closed_exactly_when_dispatch_refuses_an_unknown_key(
    policy: UnknownArguments,
) -> None:
    """Asserted against dispatch as well, so the schema and the refusal cannot come apart."""
    server = _server(unknown_arguments=policy)

    out = server.call_tool("bulk", {"items": [_ROW, {**_OTHER, "note": "x"}]}, user=None)

    items_closed = _schema(server)["properties"]["items"]["items"].get("additionalProperties")
    assert (items_closed is False) is out.is_error


def test_arguments_that_dispatch_accepts_conform_to_the_advertised_schema() -> None:
    """The schema checked against a real validator in both directions: a call that
    succeeds conforms, and an argument beside the list, which dispatch refuses, does
    not."""
    schema = _schema(_server())

    jsonschema.validate({"items": [_ROW]}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"items": [_ROW], "note": "x"}, schema)


def test_the_served_list_conforms_to_the_advertised_output_schema() -> None:
    """Read off the wire: ``structuredContent`` is the bare array, as for every
    unpaginated list result this server serves, and the text block encodes the
    same array."""
    server = _server(include_output_schema=True)

    listed: Any = _post(server, "tools/list", {})["result"]
    served: Any = _call_over_http(server, {"items": [_ROW, _OTHER]})

    (tool,) = listed["tools"]
    assert tool["outputSchema"]["type"] == "array"
    assert tool["outputSchema"]["items"]["properties"].keys() == {"number", "amount_cents"}
    assert served.get("isError") is not True, served
    assert served["structuredContent"] == [_ROW, _OTHER]
    assert json.loads(served["content"][0]["text"]) == [_ROW, _OTHER]
    assert_tool_result_conforms(tool, served)
