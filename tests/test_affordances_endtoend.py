"""Affordances on a kernel tool, on the wire: the advertised schema names the key it returns.

``assert_tool_result_conforms`` cannot see this gap, which is the reason for the
test. It validates a result against the schema, and a schema that does not forbid
extra keys validates a key it never mentions, so a result carrying an
``affordances`` object its tool never advertised conforms. Here the question is
the stronger one a client or a model asks of a schema: is every key I receive one
it describes?

Asked of kernel specs: a retrieve, a list and a paged list selector tool, and a
service tool presenting through its output selector, each declaring ``cancel``.
A kernel selector carries affordance answers on model instances and mappings, and
its ``DataclassPresenter`` reads by attribute, so the row is an ``Invoice`` where
the drf-services original rendered a mapping. The declarations are local, where
the original read ``tests/testapp/affordances.py``, which builds drf-services
specs. The original is kept beside this file as
``test_affordances_endtoend_drfs.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django_service_specs import (
    Affordance,
    DataclassPresenter,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.testing import assert_tool_result_conforms
from tests.testapp.models import Invoice

OPEN = [Unrestricted()]


@dataclass
class _Order:
    number: str


CANCEL_ORDER = ServiceSpec(
    service=lambda: None,
    permissions=OPEN,
    affordances=[
        Affordance(code="already_shipped", reason="The order has shipped.", when=lambda: True),
        Affordance(code="books_closed", reason="The books are closed.", when=lambda: False),
    ],
)
"""Met on its first condition and refused on its second, so every call answers
``books_closed``. Two conditions, so a schema enumerating only the one a row
happened to fail is told apart from one enumerating the declaration."""


def _order_selector_spec(kind: SelectorKind, **overrides: Any) -> SelectorSpec:
    declared: dict[str, Any] = {
        "kind": kind,
        "selector": (
            (lambda: Invoice.objects.get())
            if kind is SelectorKind.RETRIEVE
            else (lambda: Invoice.objects.all())
        ),
        "presenter": DataclassPresenter(_Order),
        "permissions": OPEN,
        "affordances": {"cancel": CANCEL_ORDER},
        **overrides,
    }
    return SelectorSpec(**declared)


def _server() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(_order_selector_spec(SelectorKind.RETRIEVE), name="get_order")
    server.register_tool(_order_selector_spec(SelectorKind.LIST), name="list_orders")
    server.register_tool(_order_selector_spec(SelectorKind.LIST), name="page_orders", paginate=True)
    server.register_tool(
        ServiceSpec(
            service=lambda: Invoice.objects.get(),
            permissions=OPEN,
            output_selector_spec=_order_selector_spec(
                SelectorKind.RETRIEVE, selector=lambda *, result: result
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
    # ...the conformance helper passes, as it would while the schema was silent...
    assert_tool_result_conforms(tool, result)
    # ...and only this tells a described key from a tolerated one.
    assert _undescribed(tool["outputSchema"], structured) == []
