"""``tools/list`` advertises the ``affordances`` object a tool's results carry.

drf-services renders declared ``affordances`` into every item as an
``affordances`` key that no serializer declares, so a schema derived from the
serializer alone never names it. Nothing failed: the schema does not forbid extra
keys, so every result still conformed, and a client reading the schema could not
learn the key or the codes it switches on.

Each test reads the listing through ``MCPServer.list_tools``, the public surface,
so it runs unchanged against a tree without the fix.

The kernel version of the drf-services original, kept beside it as
``test_tools_list_affordances_drfs.py``. The same questions, of kernel specs
registered with ``register_tool``: a ``DataclassPresenter`` stands where the
original's ``OrderSerializer`` stood, and the order spec and its ``cancel``
affordance are the kernel twins in ``tests/testapp/affordances.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from django_service_specs import (
    DataclassPresenter,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from tests.testapp.affordances import (
    CANCEL_ORDER,
    DECLARED_CODES,
    Order,
    fresh_server,
    order_selector_spec,
)

_ORDER_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {"number": {"type": "string"}},
    "required": ["number"],
}
"""The item schema ``Order`` advertises without affordances."""

_ORDER_SERVICE_RESULT: dict[str, Any] = {
    "type": "object",
    "properties": {"number": {"type": "string"}},
    "anyOf": [{"required": ["number"]}, {"maxProperties": 0}],
}
"""The same item as a single-row service tool advertises it.

Its output re-read can find nothing, which is served as ``{}``, so the item's
``required`` moves into an ``anyOf`` beside the empty object."""


def _required(schema: Any) -> Any:
    """The item's ``required``, wherever ``may_be_empty`` put it."""
    return schema["required"] if "required" in schema else schema["anyOf"][0]["required"]


def _output_schema(server: MCPServer, name: str) -> Any:
    listing: Any = server.list_tools(user=None)
    return next(tool for tool in listing["tools"] if tool["name"] == name).get("outputSchema")


def _assert_advertises_cancel(item: Any) -> None:
    assert "affordances" in _required(item)
    assert item["properties"]["number"] == {"type": "string"}
    cancel = item["properties"]["affordances"]["properties"]["cancel"]
    assert cancel["required"] == ["available"]
    # Enumerated from the declaration, not from any one answer, so a client can
    # switch on the code exhaustively.
    assert cancel["properties"]["code"]["enum"] == DECLARED_CODES


def _order_service(**overrides: Any) -> ServiceSpec:
    fields: dict[str, Any] = {
        "service": lambda: {"number": "A-1"},
        "permissions": [Unrestricted()],
        "output_selector_spec": order_selector_spec(
            SelectorKind.RETRIEVE, selector=lambda result: result
        ),
        **overrides,
    }
    return ServiceSpec(**fields)


def test_a_retrieve_selector_tool_advertises_its_affordances() -> None:
    server = fresh_server()
    server.register_tool(
        name="get_order",
        spec=order_selector_spec(SelectorKind.RETRIEVE),
    )

    _assert_advertises_cancel(_output_schema(server, "get_order"))


def test_an_unpaginated_list_tool_advertises_them_on_each_array_item() -> None:
    server = fresh_server()
    server.register_tool(
        name="list_orders",
        spec=order_selector_spec(SelectorKind.LIST),
    )
    schema = _output_schema(server, "list_orders")

    assert schema["type"] == "array"
    _assert_advertises_cancel(schema["items"])


def test_a_paginated_list_tool_advertises_them_on_each_envelope_item() -> None:
    """On the items, and not on the envelope, which is this transport's own shape."""
    server = fresh_server()
    server.register_tool(
        name="page_orders",
        spec=order_selector_spec(SelectorKind.LIST),
        paginate=True,
    )
    schema = _output_schema(server, "page_orders")

    assert set(schema["properties"]) == {"items", "page", "totalPages", "hasNext"}
    assert schema["required"] == ["items", "page", "totalPages", "hasNext"]
    _assert_advertises_cancel(schema["properties"]["items"]["items"])


def test_a_service_tool_advertises_its_output_selector_spec_s_affordances() -> None:
    server = fresh_server()
    server.register_tool(name="place_order", spec=_order_service())

    _assert_advertises_cancel(_output_schema(server, "place_order"))


def test_a_service_s_own_affordances_are_not_advertised() -> None:
    """A service's own ``affordances`` are what a call is refused against, and are
    never rendered, so they must not reach the schema of what it returns."""
    server = fresh_server()
    server.register_tool(
        name="cancel_order",
        spec=_order_service(
            affordances=CANCEL_ORDER.affordances,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda result: result,
                presenter=DataclassPresenter(Order),
                permissions=[Unrestricted()],
            ),
        ),
        # Its ``books_closed`` condition is unmet, which on its own would leave
        # it out of the listing this test reads.
        always_listed=True,
    )

    assert _output_schema(server, "cancel_order") == _ORDER_SERVICE_RESULT


@pytest.mark.parametrize(
    ("kind", "paginate", "expected"),
    [
        pytest.param(SelectorKind.RETRIEVE, False, _ORDER_ITEM, id="retrieve"),
        pytest.param(SelectorKind.LIST, False, {"type": "array", "items": _ORDER_ITEM}, id="list"),
        pytest.param(
            SelectorKind.LIST,
            True,
            {
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": _ORDER_ITEM},
                    "page": {"type": "integer"},
                    "totalPages": {"type": "integer"},
                    "hasNext": {"type": "boolean"},
                },
                "required": ["items", "page", "totalPages", "hasNext"],
            },
            id="paginated",
        ),
    ],
)
def test_a_spec_without_affordances_advertises_exactly_what_it_did_before(
    kind: SelectorKind, paginate: bool, expected: dict[str, Any]
) -> None:
    server = fresh_server()
    server.register_tool(
        name="orders",
        spec=order_selector_spec(kind, affordances=None),
        paginate=paginate,
    )
    server.register_tool(
        name="place_order",
        spec=_order_service(
            output_selector_spec=order_selector_spec(
                SelectorKind.RETRIEVE, selector=lambda result: result, affordances=None
            )
        ),
    )

    assert _output_schema(server, "orders") == expected
    assert _output_schema(server, "place_order") == _ORDER_SERVICE_RESULT
