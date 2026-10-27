"""What a kernel ``many`` service spec needs before it becomes a tool.

A ``many=True`` spec validates a list of rows, and MCP ``arguments`` is always a
JSON object, so the list travels under the one argument ``many_argument`` names.
Asked of kernel specs registered with ``register_tool`` and ``register_tools``:
such a spec registers, advertises the list under that name and delivers the
validated rows to ``data``; and the declarations that would stop the list
reaching dispatch are refused by the kernel, a read taking the argument's name
and a target lookup beside the list.

The drf-services original also refused a ``UrlKwarg`` or ``QueryParam`` taking
the argument's name and a spreading ``ArgumentBinding``; a kernel spec has
neither, so those have no counterpart. It is kept beside this file as
``test_service_to_tool_drfs.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from tests.testapp.kernel_shapes import InvoiceInput
from tests.utils import tool_error

_ROWS = [{"number": "A"}, {"number": "B", "amount_cents": 3}]


def _numbers(*, data: list[InvoiceInput]) -> list[str]:
    return [row.number for row in data]


def _many(**declared: Any) -> ServiceSpec:
    return ServiceSpec(
        service=_numbers,
        atomic=False,
        permissions=[Unrestricted()],
        validator=DataclassValidator(InvoiceInput),
        many=True,
        **declared,
    )


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _registered(spec: ServiceSpec) -> MCPServer:
    server = _server()
    server.register_tool(spec, name="t", description="Record invoices.")
    return server


@pytest.mark.parametrize("argument", ["items", "rows"])
def test_a_many_spec_advertises_the_list_under_its_argument(argument: str) -> None:
    server = _registered(_many(many_argument=argument))
    listed: Any = server.list_tools(user=None)

    schema = listed["tools"][0]["inputSchema"]

    assert set(schema["properties"]) == {argument}
    assert schema["required"] == [argument]
    assert schema["properties"][argument]["type"] == "array"
    assert schema["properties"][argument]["items"]["required"] == ["number"]


@pytest.mark.parametrize("argument", ["items", "rows"])
def test_a_many_spec_delivers_the_validated_rows_to_data(argument: str) -> None:
    server = _registered(_many(many_argument=argument))

    out = server.call_tool("t", {argument: _ROWS}, user=None).to_dict()

    assert out.get("isError") is not True, out
    assert out["structuredContent"] == ["A", "B"]


def test_a_payload_that_is_not_a_list_is_a_validation_error() -> None:
    server = _registered(_many())

    out = server.call_tool("t", {"items": {"number": "A"}}, user=None).to_dict()

    assert tool_error(out)["type"] == "validation_error"


def test_a_many_spec_in_a_registry_registers() -> None:
    registry = SpecRegistry()
    registry.register("invoices.record", _many())

    (binding,) = _server().register_tools(registry)

    assert binding.name == "invoices.record"


def test_a_read_named_as_the_list_argument_is_refused() -> None:
    # The kernel refuses two declarations of one name when the spec's
    # parameters are assembled, which ``register_tool`` does to compile the
    # schema, so the list can never be shadowed by a read of its name.
    with pytest.raises(ImproperlyConfigured, match="'items'"):
        _registered(_many(reads=Parameters([Parameter("items", "string")])))


def test_a_single_item_spec_may_read_an_argument_named_items() -> None:
    spec = ServiceSpec(
        service=lambda *, items: items,
        atomic=False,
        permissions=[Unrestricted()],
        reads=Parameters([Parameter("items", "string", required=True)]),
    )

    out = _registered(spec).call_tool("t", {"items": "x"}, user=None).to_dict()

    assert out["structuredContent"] == "x"


@pytest.mark.parametrize(
    ("declared", "named"),
    [
        (
            {"instance_selector_spec": SelectorSpec(kind=SelectorKind.RETRIEVE, selector=dict)},
            "instance_selector_spec",
        ),
        (
            {"collection_selector_spec": SelectorSpec(kind=SelectorKind.LIST, selector=list)},
            "collection_selector_spec",
        ),
    ],
)
def test_a_lookup_beside_the_list_is_refused_when_the_spec_is_built(
    declared: dict[str, Any], named: str
) -> None:
    # A list of rows resolves no target, so dispatch would never run the lookup.
    with pytest.raises(ImproperlyConfigured, match=f"declares a {named} with many=True"):
        _many(**declared)
