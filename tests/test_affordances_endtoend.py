from __future__ import annotations

from typing import Any

import pytest
from django_service_specs import (
    SelectorKind,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.testing import assert_tool_result_conforms
from tests.testapp.affordances import fresh_server, order_selector_spec
from tests.testapp.models import Invoice

OPEN = [Unrestricted()]


def _server() -> MCPServer:
    server = fresh_server()
    server.register_tool(order_selector_spec(SelectorKind.RETRIEVE), name="get_order")
    server.register_tool(order_selector_spec(SelectorKind.LIST), name="list_orders")
    server.register_tool(order_selector_spec(SelectorKind.LIST), name="page_orders", paginate=True)
    server.register_tool(
        ServiceSpec(
            service=lambda: Invoice.objects.get(),
            permissions=OPEN,
            output_selector_spec=order_selector_spec(
                SelectorKind.RETRIEVE, selector=lambda *, result: result, permissions=None
            ),
        ),
        name="place_order",
    )
    return server


def _undescribed(schema: Any, value: Any, path: str = "$") -> list[str]:
    """Every key in ``value`` that ``schema`` does not name, and every enum it breaks."""
    if isinstance(value, dict):
        properties: dict[str, Any] = schema.get("properties", {})
        found: list[str] = []
        for key, child in value.items():
            if key in properties:
                found.extend(_undescribed(properties[key], child, f"{path}.{key}"))
            else:
                found.append(f"{path}.{key}")
        return found
    if isinstance(value, list):
        return [
            problem
            for index, item in enumerate(value)
            for problem in _undescribed(schema["items"], item, f"{path}[{index}]")
        ]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}={value!r}"]
    return []


def _answers(structured: Any) -> list[Any]:
    """The ``affordances`` object on each presented item, wherever the item sits."""
    if isinstance(structured, list):
        items = structured
    elif "items" in structured:
        items = structured["items"]
    else:
        items = [structured]
    return [item["affordances"] for item in items]


@pytest.mark.parametrize("name", ["get_order", "list_orders", "page_orders", "place_order"])
@pytest.mark.django_db(transaction=True)
async def test_the_advertised_schema_describes_the_affordances_a_call_returns(name: str) -> None:
    await Invoice.objects.acreate(number="A-1")
    server = _server()
    listing: Any = await server.alist_tools(user=None)
    tool = next(entry for entry in listing["tools"] if entry["name"] == name)

    result: Any = await server.acall_tool(name, {}, user=None)
    structured = result["structuredContent"]

    # The key is really there, refused on the second condition...
    assert _answers(structured) == [
        {"cancel": {"available": False, "code": "books_closed", "reason": "The books are closed."}}
    ]
    assert_tool_result_conforms(tool, result)
    assert _undescribed(tool["outputSchema"], structured) == []
