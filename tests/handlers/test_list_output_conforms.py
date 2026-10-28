"""A kernel tool whose result presents as a list advertises, and serves, a list.

A service tool whose ``output_selector_spec`` re-fetches a ``LIST`` must not
advertise the bare item schema while serving an array. Every case registers a
kernel spec with
``include_output_schema=True``, calls it, and runs the served payload through
``assert_tool_result_conforms``, which is what a client holding the schema
would check, with each ``RETRIEVE`` sibling riding along so a fix answering every
tool with an array fails too.

The cases:

- a selector tool, and a service tool re-fetching through an output selector,
  of either kind;
- a service declaring ``returns_many``, the kernel's form of a ``LIST`` output
  declaration with no selector: it presents the service's own return as the
  set;
- a ``many`` service, whose rows are its result. Its output selector re-reads
  what the bulk write produced, so the selector's kind decides what is served.

A ``LIST`` declaration with no selector is not asked to refuse, by name, a
return that is no set: the kernel's ``returns_many`` iterates whatever the
service returns, so a mapping is presented as its keys and fails in the
presenter, and an ``int`` as not iterable. Refusing it by name is the kernel's
question, not this transport's.
"""

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
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.testing import assert_tool_result_conforms

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
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
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
        pytest.param(SelectorKind.LIST, [_ROW], id="list"),
        pytest.param(SelectorKind.RETRIEVE, _ROW, id="retrieve"),
    ],
)


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
    # Named outright as well: a schema loose enough to accept both shapes would
    # conform either way, and the shape is the contract.
    assert tool["outputSchema"]["type"] == ("array" if kind is SelectorKind.LIST else "object")


async def test_a_service_returning_many_renders_and_advertises_a_list() -> None:
    spec = ServiceSpec(
        service=lambda: [_Order(**_ROW)],
        atomic=False,
        returns_many=True,
        presenter=DataclassPresenter(_Order),
        permissions=OPEN,
    )

    tool, result = await _listed_and_served(_server(spec))

    assert result["structuredContent"] == [_ROW]
    assert tool["outputSchema"]["type"] == "array"


def _placed(*, data: list[_Order]) -> list[_Order]:
    return list(data)


async def test_a_many_service_serves_its_rows_as_an_array() -> None:
    spec = ServiceSpec(
        service=_placed,
        atomic=False,
        many=True,
        validator=DataclassValidator(_Order),
        presenter=DataclassPresenter(_Order),
        permissions=OPEN,
    )

    tool, result = await _listed_and_served(_server(spec), {"items": [_ROW, _ROW]})

    assert result["structuredContent"] == [_ROW, _ROW]
    assert tool["outputSchema"]["type"] == "array"


_RE_FETCHED: dict[str, Any] = {"number": "Z-9"}


@pytest.mark.parametrize(
    ("kind", "served"),
    [
        pytest.param(SelectorKind.LIST, [_RE_FETCHED], id="list"),
        pytest.param(SelectorKind.RETRIEVE, _RE_FETCHED, id="retrieve"),
    ],
)
async def test_a_many_services_output_selector_re_reads_and_its_kind_decides(
    kind: SelectorKind, served: Any
) -> None:
    # The output selector re-reads what the bulk write produced, and its kind
    # decides what is presented, so the schema follows the kind and the served
    # payload conforms to it either way.
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
    assert tool["outputSchema"]["type"] == ("array" if kind is SelectorKind.LIST else "object")
