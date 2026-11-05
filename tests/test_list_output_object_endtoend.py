"""A result that presents as a list is served as an object, and its schema says so.

MCP 2025-11-25 declares ``outputSchema.type`` as ``"object"`` and
``structuredContent`` as ``{ [key: string]: unknown }``. A list tool served
unpaged, a ``many`` service and a ``returns_many`` one each present a list, so
the list travels under ``items``, the key the paged envelope already uses:
``{"items": [...]}`` as ``structuredContent`` and as the text block, against an
``outputSchema`` whose root is an object holding the array under ``items``.

Each entry point is driven, because each answers on its own path: the sync wire
handler, the in-process ``call_tool`` and ``acall_tool``. What a client checks is
the result against the listing, so every result is validated against the schema
``tools/list`` advertised for it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.http import HttpRequest
from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer, ToolContentKind
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_list import handle_tools_list
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.server.utils import UnboundedListWarning
from django_spec_mcp.testing import assert_tool_result_conforms
from tests.testapp.kernel_shapes import InvoiceInput, InvoiceOutput
from tests.testapp.models import Invoice
from tests.testapp.services import list_invoices

OPEN = [Unrestricted()]
INVOICE = DataclassPresenter(InvoiceOutput)

pytestmark = pytest.mark.filterwarnings(
    f"ignore::{UnboundedListWarning.__module__}.UnboundedListWarning"
)


@dataclass
class _Link:
    uri: str
    name: str


def _record_many(*, data: list[InvoiceInput]) -> list[Invoice]:
    return [
        Invoice.objects.create(number=row.number, amount_cents=row.amount_cents) for row in data
    ]


def _no_invoices() -> list[Invoice] | None:
    return None


def _links() -> list[_Link]:
    return [
        _Link(uri="https://example.test/a", name="a"),
        _Link(uri="https://example.test/b", name="b"),
    ]


def _server() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.LIST, selector=list_invoices, presenter=INVOICE, permissions=OPEN
        ),
        name="invoices.list",
        description="d",
    )
    server.register_tool(
        ServiceSpec(
            service=_record_many,
            validator=DataclassValidator(InvoiceInput),
            presenter=INVOICE,
            permissions=OPEN,
            many=True,
        ),
        name="invoices.create_many",
        description="d",
    )
    server.register_tool(
        ServiceSpec(
            service=_no_invoices,
            returns_many=True,
            allow_none=True,
            presenter=INVOICE,
            permissions=OPEN,
        ),
        name="invoices.returned",
        description="d",
    )
    server.register_tool(
        ServiceSpec(
            service=_links, returns_many=True, presenter=DataclassPresenter(_Link), permissions=OPEN
        ),
        name="links",
        description="d",
        content_kind=ToolContentKind.RESOURCE_LINK,
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


def _tool(server: MCPServer, name: str) -> dict[str, Any]:
    listed: Any = handle_tools_list(None, _ctx(server))
    return next(t for t in listed["tools"] if t["name"] == name)


def _wire(server: MCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    out: Any = handle_tools_call({"name": name, "arguments": arguments}, _ctx(server))
    return out


def _in_process(server: MCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return server.call_tool(name, arguments, user=None).to_dict()


def _async_wire(server: MCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    out: Any = async_to_sync(server.acall_tool)(name, arguments, user=None)
    return out


_ENTRY_POINTS = pytest.mark.parametrize(
    "call", [_wire, _in_process, _async_wire], ids=["wire", "call_tool", "acall_tool"]
)


def _assert_an_object_of_items(tool: dict[str, Any], result: dict[str, Any]) -> list[Any]:
    assert not result.get("isError"), result
    structured = result["structuredContent"]
    assert isinstance(structured, dict)
    assert set(structured) == {"items"}
    # The text block carries the same JSON as the structured content.
    assert json.loads(result["content"][0]["text"]) == structured
    assert tool["outputSchema"]["type"] == "object"
    assert tool["outputSchema"]["required"] == ["items"]
    assert_tool_result_conforms(tool, result)
    return structured["items"]


@_ENTRY_POINTS
@pytest.mark.django_db(transaction=True)
def test_an_unpaged_list_is_served_as_an_object_of_items(call: Any) -> None:
    server = _server()
    Invoice.objects.create(number="INV-1", amount_cents=5)
    Invoice.objects.create(number="INV-2", amount_cents=7)

    result = call(server, "invoices.list", {})

    items = _assert_an_object_of_items(_tool(server, "invoices.list"), result)
    assert [row["number"] for row in items] == ["INV-1", "INV-2"]


@_ENTRY_POINTS
@pytest.mark.django_db(transaction=True)
def test_a_many_service_is_served_as_an_object_of_items(call: Any) -> None:
    server = _server()

    result = call(
        server,
        "invoices.create_many",
        {"items": [{"number": "A", "amount_cents": 1}, {"number": "B", "amount_cents": 2}]},
    )

    items = _assert_an_object_of_items(_tool(server, "invoices.create_many"), result)
    assert [row["number"] for row in items] == ["A", "B"]


@_ENTRY_POINTS
@pytest.mark.django_db(transaction=True)
def test_a_list_presenting_nothing_is_an_object_of_no_items(call: Any) -> None:
    server = _server()

    result = call(server, "invoices.returned", {})

    assert _assert_an_object_of_items(_tool(server, "invoices.returned"), result) == []


@_ENTRY_POINTS
@pytest.mark.django_db(transaction=True)
def test_a_list_of_resource_links_keeps_its_blocks_and_wraps_its_structured_content(
    call: Any,
) -> None:
    # The blocks are built from the presented list, one link per row; only the
    # structured content is wrapped.
    server = _server()

    result = call(server, "links", {})

    assert not result.get("isError"), result
    assert [block["uri"] for block in result["content"]] == [
        "https://example.test/a",
        "https://example.test/b",
    ]
    assert result["structuredContent"] == {
        "items": [
            {"uri": "https://example.test/a", "name": "a"},
            {"uri": "https://example.test/b", "name": "b"},
        ]
    }
    tool = _tool(server, "links")
    assert tool["outputSchema"]["type"] == "object"
    assert_tool_result_conforms(tool, result)
