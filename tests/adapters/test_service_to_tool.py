from __future__ import annotations

import json
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
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
from tests.testapp.kernel_shapes import InvoiceInput
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import LEGACY, make_server, post_in_era, tool_error

_ROWS = [{"number": "A"}, {"number": "B", "amount_cents": 3}]


def _numbers(*, data: list[InvoiceInput]) -> list[str]:
    return [row.number for row in data]


def _many(**declared: Any) -> ServiceSpec:
    defaults: dict[str, Any] = {"service": _numbers, "atomic": False, "many": True}
    return ServiceSpec(
        permissions=[Unrestricted()],
        validator=DataclassValidator(InvoiceInput),
        **{**defaults, **declared},
    )


def _registered(spec: ServiceSpec) -> MCPServer:
    server = make_server()
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
    assert out["structuredContent"] == {"items": ["A", "B"]}


def test_a_payload_that_is_not_a_list_is_a_validation_error() -> None:
    server = _registered(_many())

    out = server.call_tool("t", {"items": {"number": "A"}}, user=None).to_dict()

    assert tool_error(out)["type"] == "validation_error"


def test_a_row_that_fails_validation_is_named_by_its_index_in_the_detail() -> None:
    # The kernel keys the detail by the failing row's index under the argument
    server = _registered(_many())

    out = server.call_tool(
        "t", {"items": [{"number": "A"}, {"amount_cents": 2}]}, user=None
    ).to_dict()

    error = tool_error(out)
    assert error["type"] == "validation_error"
    assert error["detail"] == {"items": {"1": {"number": ["This field is required."]}}}


def test_a_many_spec_may_read_a_name_other_than_the_one_it_declares_for_the_list() -> None:
    # The collision is with ``many_argument``, not with the default: under
    spec = _many(many_argument="rows", reads=Parameters([Parameter("items", "string")]))

    listed: Any = _registered(spec).list_tools(user=None)
    schema = listed["tools"][0]["inputSchema"]

    assert set(schema["properties"]) == {"rows", "items"}
    with pytest.raises(ImproperlyConfigured, match="'rows'"):
        _registered(_many(many_argument="rows", reads=Parameters([Parameter("rows", "string")])))


def test_a_many_spec_in_a_registry_registers() -> None:
    registry = SpecRegistry()
    registry.register("invoices.record", _many())

    (binding,) = make_server().register_tools(registry)

    assert binding.name == "invoices.record"


def test_a_read_named_as_the_list_argument_is_refused() -> None:
    # The kernel refuses two declarations of one name when the spec's
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

    assert json.loads(out["content"][0]["text"]) == "x"


def test_an_instance_lookup_beside_the_list_is_refused_when_the_spec_is_built() -> None:
    lookup = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=dict)

    with pytest.raises(ImproperlyConfigured, match=r"instance_selector_spec with many=True"):
        _many(instance_selector_spec=lookup)


def _numbers_into(*, collection: list[str], data: list[InvoiceInput]) -> list[str]:
    return [*collection, *(row.number for row in data)]


def _shelf(*, shelf: str) -> list[str]:
    return [f"{shelf}-0"]


def _many_over_a_collection() -> ServiceSpec:
    return _many(
        service=_numbers_into,
        collection_selector_spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_shelf,
            reads=Parameters([Parameter("shelf", "string", required=True)]),
        ),
    )


def test_a_many_tool_over_a_collection_advertises_the_lookup_beside_the_list() -> None:
    listed: Any = _registered(_many_over_a_collection()).list_tools(user=None)

    schema = listed["tools"][0]["inputSchema"]

    assert set(schema["properties"]) == {"items", "shelf"}
    assert set(schema["required"]) == {"items", "shelf"}
    assert schema["properties"]["shelf"] == {"type": "string"}


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_a_many_tool_over_a_collection_hands_the_service_the_collection(is_async: bool) -> None:
    server = _registered(_many_over_a_collection())
    arguments = {"shelf": "s", "items": _ROWS}

    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        out = post_in_era(LEGACY, is_async, "tools/call", {"name": "t", "arguments": arguments})

    assert out.get("isError") is not True, out
    assert out["structuredContent"] == {"items": ["s-0", "A", "B"]}
