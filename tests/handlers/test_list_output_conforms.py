from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.testing import assert_tool_result_conforms
from tests.utils import make_server

pytestmark = pytest.mark.django_db(transaction=True)

OPEN = [Unrestricted()]
_ROW: dict[str, Any] = {"number": "A-1"}


@dataclass
class _Order:
    number: str


def _rows(kind: SelectorKind, row: dict[str, Any] = _ROW) -> Any:
    return [_Order(**row)] if kind is SelectorKind.LIST else _Order(**row)


def _orders(kind: SelectorKind, row: dict[str, Any] = _ROW, **spec: Any) -> SelectorSpec:
    return SelectorSpec(
        kind=kind, selector=lambda: _rows(kind, row), presenter=DataclassPresenter(_Order), **spec
    )


def _server(spec: Any) -> MCPServer:
    server = make_server(session_store=None)
    server.register_tool(spec, name="orders", include_output_schema=True)
    return server


async def _listed_and_served(server: MCPServer, arguments: Any = None) -> tuple[Any, Any]:
    listing: Any = server.list_tools(user=None)
    tool: Any = next(entry for entry in listing["tools"] if entry["name"] == "orders")
    result: Any = await server.acall_tool("orders", arguments, user=None)
    assert result.get("isError") is not True, result
    assert_tool_result_conforms(tool, result)
    return tool, result


_KINDS = pytest.mark.parametrize(
    ("kind", "served"),
    [
        pytest.param(SelectorKind.LIST, {"items": [_ROW]}, id="list"),
        pytest.param(SelectorKind.RETRIEVE, _ROW, id="retrieve"),
    ],
)


def _rows_schema(tool: Any, kind: SelectorKind) -> None:
    # Named outright as well: a schema loose enough to accept both shapes would
    schema = tool["outputSchema"]
    assert schema["type"] == "object"
    if kind is SelectorKind.LIST:
        assert schema["required"] == ["items"]
        assert schema["properties"]["items"]["type"] == "array"
    else:
        assert "items" not in schema["properties"]


@_KINDS
@pytest.mark.parametrize("shape", ["selector_tool", "service_tool"])
async def test_the_served_payload_conforms_to_the_advertised_schema(
    shape: str, kind: SelectorKind, served: Any
) -> None:
    if shape == "selector_tool":
        spec: Any = _orders(kind, permissions=OPEN)
    else:
        spec = ServiceSpec(
            service=lambda: None, atomic=False, output_selector_spec=_orders(kind), permissions=OPEN
        )

    tool, result = await _listed_and_served(_server(spec))

    assert result["structuredContent"] == served
    _rows_schema(tool, kind)


def _placed(*, data: list[_Order]) -> list[_Order]:
    return list(data)


@pytest.mark.parametrize(
    ("spec", "arguments", "served"),
    [
        pytest.param(
            ServiceSpec(
                service=lambda: [_Order(**_ROW)],
                atomic=False,
                returns_many=True,
                presenter=DataclassPresenter(_Order),
                permissions=OPEN,
            ),
            None,
            {"items": [_ROW]},
            id="returns-many",
        ),
        pytest.param(
            ServiceSpec(
                service=_placed,
                atomic=False,
                many=True,
                validator=DataclassValidator(_Order),
                presenter=DataclassPresenter(_Order),
                permissions=OPEN,
            ),
            {"items": [_ROW, _ROW]},
            {"items": [_ROW, _ROW]},
            id="many",
        ),
    ],
)
async def test_a_service_over_many_rows_serves_and_advertises_an_array_under_items(
    spec: ServiceSpec, arguments: dict[str, Any] | None, served: dict[str, Any]
) -> None:
    tool, result = await _listed_and_served(_server(spec), arguments)

    assert result["structuredContent"] == served
    _rows_schema(tool, SelectorKind.LIST)


_RE_FETCHED: dict[str, Any] = {"number": "Z-9"}


@pytest.mark.parametrize(
    ("kind", "served"),
    [
        pytest.param(SelectorKind.LIST, {"items": [_RE_FETCHED]}, id="list"),
        pytest.param(SelectorKind.RETRIEVE, _RE_FETCHED, id="retrieve"),
    ],
)
async def test_a_many_services_output_selector_re_reads_and_its_kind_decides(
    kind: SelectorKind, served: Any
) -> None:
    # The output selector re-reads what the bulk write produced, and its kind
    spec = ServiceSpec(
        service=_placed,
        atomic=False,
        many=True,
        validator=DataclassValidator(_Order),
        output_selector_spec=_orders(kind, _RE_FETCHED),
        permissions=OPEN,
    )

    tool, result = await _listed_and_served(_server(spec), {"items": [_ROW, _ROW]})

    assert result["structuredContent"] == served
    _rows_schema(tool, kind)
