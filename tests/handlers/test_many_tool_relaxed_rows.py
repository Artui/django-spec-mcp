from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jsonschema
import pytest
from asgiref.sync import sync_to_async
from django_service_specs import UNSET, DataclassValidator, ServiceSpec, Unrestricted, UnsetType
from typing_extensions import TypedDict

from django_spec_mcp import MCPServer
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from tests.utils import call_context, make_server, tool_error

pytestmark = pytest.mark.django_db(transaction=True)


@dataclass
class _Line:
    number: str
    customer: str


class _Customer(TypedDict):
    customer: str


def _customer() -> _Customer:
    return {"customer": "acme"}


def _issue(*, data: list[_Line]) -> list[str]:
    return [f"{line.customer}:{line.number}" for line in data]


def _server(input_data: Callable[[], Any] = _customer) -> MCPServer:
    server = make_server(session_store=None)
    server.register_tool(
        ServiceSpec(
            service=_issue,
            many=True,
            atomic=False,
            validator=DataclassValidator(_Line),
            input_data=input_data,
            permissions=[Unrestricted()],
        ),
        name="bulk",
        description="Issue lines.",
    )
    return server


def _schema(server: MCPServer) -> dict[str, Any]:
    listed: Any = server.list_tools(user=None)
    return listed["tools"][0]["inputSchema"]


async def _call(server: MCPServer, route: str, arguments: dict[str, Any]) -> Any:
    params = {"name": "bulk", "arguments": arguments}
    if route == "async_handler":
        return await handle_tools_call_async(params, call_context(server))
    return await sync_to_async(handle_tools_call)(params, call_context(server))


@pytest.mark.parametrize("route", ["handler", "async_handler"])
async def test_a_row_omitting_a_field_input_data_fills_conforms_and_dispatches(
    route: str,
) -> None:
    server = _server()
    arguments = {"items": [{"number": "A-1"}, {"number": "A-2"}]}

    jsonschema.validate(arguments, _schema(server))
    out = await _call(server, route, arguments)

    assert out.get("isError") is not True, out
    assert out["structuredContent"] == {"items": ["acme:A-1", "acme:A-2"]}


@pytest.mark.parametrize("route", ["handler", "async_handler"])
async def test_a_row_omitting_a_required_field_fails_the_schema_and_is_refused_at_its_index(
    route: str,
) -> None:
    server = _server()
    arguments: dict[str, Any] = {"items": [{"number": "A-1"}, {}]}

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(arguments, _schema(server))
    error = tool_error(await _call(server, route, arguments))

    assert error["type"] == "validation_error"
    assert error["detail"] == {"items": {"1": {"number": ["This field is required."]}}}


class _CustomerAndMayFillNumber(TypedDict):
    customer: str
    number: str | UnsetType


def _fills_both() -> _CustomerAndMayFillNumber:
    return {"customer": "acme", "number": "auto-1"}


def _declines_number_for_acme() -> _CustomerAndMayFillNumber:
    return {"customer": "acme", "number": UNSET}


@pytest.mark.parametrize("route", ["handler", "async_handler"])
async def test_a_row_omitting_a_field_input_data_may_fill_conforms_when_it_fills_it(
    route: str,
) -> None:
    server = _server(_fills_both)
    arguments = {"items": [{}, {}]}

    jsonschema.validate(arguments, _schema(server))
    out = await _call(server, route, arguments)

    assert out.get("isError") is not True, out
    assert out["structuredContent"] == {"items": ["acme:auto-1", "acme:auto-1"]}


@pytest.mark.parametrize("route", ["handler", "async_handler"])
async def test_a_row_omitting_a_field_input_data_declines_is_refused_at_its_index(
    route: str,
) -> None:
    server = _server(_declines_number_for_acme)
    arguments: dict[str, Any] = {"items": [{}]}

    # The field is offered and not required, so the schema cannot tell the two
    jsonschema.validate(arguments, _schema(server))
    error = tool_error(await _call(server, route, arguments))

    assert error["type"] == "validation_error"
    assert error["detail"] == {"items": {"0": {"number": ["This field is required."]}}}
