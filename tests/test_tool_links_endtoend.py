from __future__ import annotations

from typing import Any

import pytest
from django_service_specs import (
    Affordance,
    Parameter,
    Parameters,
    SelectorKind,
    ServerArguments,
    ServiceSpec,
    SpecLink,
    SpecRegistry,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.registry.tool_registry import ToolRegistry
from django_spec_mcp.testing import assert_tool_result_conforms
from tests.testapp.affordances import fresh_server, order_selector_spec
from tests.testapp.models import Invoice

OPEN = [Unrestricted()]
NUMBER = Parameter("number", "string", required=True)
NOTE = Parameter("note", "string")


def _void(**_kwargs: Any) -> None: ...


def _target(*, met: bool = True) -> ServiceSpec:
    return ServiceSpec(
        service=_void,
        permissions=OPEN,
        reads=Parameters((NUMBER, NOTE)),
        affordances=[Affordance(code="closed", reason="Closed.", when=lambda: met)],
    )


def _call(server: MCPServer, name: str) -> Any:
    return server.call_tool(name, {}, user=None).to_dict()


def _rows(structured: dict[str, Any]) -> list[dict[str, Any]]:
    return structured.get("items", [structured])


@pytest.mark.django_db
class TestALinkInAToolResult:
    def test_an_available_link_names_the_tool_and_its_bound_arguments(self) -> None:
        Invoice.objects.create(number="A-1")
        cancel = _target()
        server = fresh_server()
        server.register_tool(cancel, name="cancel_order")
        server.register_tool(
            order_selector_spec(SelectorKind.RETRIEVE, affordances={"cancel": cancel}),
            name="get_order",
        )

        assert _call(server, "get_order")["structuredContent"]["affordances"] == {
            "cancel": {"available": True, "tool": "cancel_order", "arguments": {"number": "A-1"}}
        }

    def test_an_unavailable_link_keeps_its_answer_and_names_no_tool(self) -> None:
        Invoice.objects.create(number="A-1")
        cancel = _target(met=False)
        server = fresh_server()
        server.register_tool(cancel, name="cancel_order")
        server.register_tool(
            order_selector_spec(SelectorKind.RETRIEVE, affordances={"cancel": cancel}),
            name="get_order",
        )

        assert _call(server, "get_order")["structuredContent"]["affordances"] == {
            "cancel": {"available": False, "code": "closed", "reason": "Closed."}
        }

    def test_a_target_no_tool_holds_is_omitted_and_keeps_its_answer(self) -> None:
        Invoice.objects.create(number="A-1")
        server = fresh_server()
        server.register_tool(
            order_selector_spec(SelectorKind.RETRIEVE, affordances={"cancel": _target()}),
            name="get_order",
        )

        assert _call(server, "get_order")["structuredContent"]["affordances"] == {
            "cancel": {"available": True}
        }

    def test_an_equal_but_distinct_spec_does_not_name_the_target(self) -> None:
        Invoice.objects.create(number="A-1")
        server = fresh_server()
        server.register_tool(_target(), name="cancel_order")
        server.register_tool(
            order_selector_spec(SelectorKind.RETRIEVE, affordances={"cancel": _target()}),
            name="get_order",
        )

        assert (
            "tool" not in _call(server, "get_order")["structuredContent"]["affordances"]["cancel"]
        )

    def test_an_explicit_spec_link_binds_its_named_field(self) -> None:
        Invoice.objects.create(number="A-1")
        cancel = _target()
        server = fresh_server()
        server.register_tool(cancel, name="cancel_order")
        link = SpecLink(cancel, arguments={"number": "number", "note": "number"})
        server.register_tool(
            order_selector_spec(SelectorKind.RETRIEVE, affordances={"cancel": link}),
            name="get_order",
        )

        entry = _call(server, "get_order")["structuredContent"]["affordances"]["cancel"]
        assert entry["arguments"] == {"number": "A-1", "note": "A-1"}

    def test_a_read_the_target_tool_fills_itself_is_left_out(self) -> None:
        Invoice.objects.create(number="A-1")
        cancel = _target()
        link = SpecLink(cancel, arguments={"number": "number", "note": "number"})
        server = fresh_server()
        server.register_tool(
            cancel, name="cancel_order", server_arguments=ServerArguments(values={"note": "fixed"})
        )
        server.register_tool(
            order_selector_spec(SelectorKind.RETRIEVE, affordances={"cancel": link}),
            name="get_order",
        )

        entry = _call(server, "get_order")["structuredContent"]["affordances"]["cancel"]
        assert entry == {"available": True, "tool": "cancel_order", "arguments": {"number": "A-1"}}

    @pytest.mark.parametrize("paginate", [False, True])
    def test_each_listed_row_carries_its_own_arguments(self, paginate: bool) -> None:
        Invoice.objects.create(number="A-1")
        Invoice.objects.create(number="A-2")
        cancel = _target()
        server = fresh_server()
        server.register_tool(cancel, name="cancel_order")
        server.register_tool(
            order_selector_spec(SelectorKind.LIST, affordances={"cancel": cancel}),
            name="list_orders",
            paginate=paginate,
        )

        rows = _rows(_call(server, "list_orders")["structuredContent"])
        assert sorted(row["affordances"]["cancel"]["arguments"]["number"] for row in rows) == [
            "A-1",
            "A-2",
        ]
        assert {row["affordances"]["cancel"]["tool"] for row in rows} == {"cancel_order"}

    def test_a_service_presenting_through_its_output_selector_links_too(self) -> None:
        Invoice.objects.create(number="A-1")
        cancel = _target()
        server = fresh_server()
        server.register_tool(cancel, name="cancel_order")
        server.register_tool(
            ServiceSpec(
                service=lambda: Invoice.objects.get(),
                permissions=OPEN,
                output_selector_spec=order_selector_spec(
                    SelectorKind.RETRIEVE,
                    selector=lambda *, result: result,
                    permissions=None,
                    affordances={"cancel": cancel},
                ),
            ),
            name="place_order",
        )

        assert _call(server, "place_order")["structuredContent"]["affordances"]["cancel"][
            "tool"
        ] == ("cancel_order")


@pytest.mark.django_db(transaction=True)
async def test_the_wire_result_carries_the_link_and_conforms_to_its_schema() -> None:
    await Invoice.objects.acreate(number="A-1")
    cancel = _target()
    server = fresh_server()
    server.register_tool(cancel, name="cancel_order")
    server.register_tool(
        order_selector_spec(SelectorKind.LIST, affordances={"cancel": cancel}),
        name="list_orders",
        paginate=True,
    )
    listing: Any = await server.alist_tools(user=None)
    tool = next(entry for entry in listing["tools"] if entry["name"] == "list_orders")

    result: Any = await server.acall_tool("list_orders", {}, user=None)

    (row,) = result["structuredContent"]["items"]
    assert row["affordances"]["cancel"]["tool"] == "cancel_order"
    assert_tool_result_conforms(tool, result)
    entry = tool["outputSchema"]["properties"]["items"]["items"]["properties"]["affordances"]
    assert entry["properties"]["cancel"]["properties"]["tool"] == {"type": "string"}
    assert entry["properties"]["cancel"]["properties"]["arguments"] == {"type": "object"}


@pytest.mark.django_db
def test_a_target_a_spec_registry_replaced_names_the_tool_holding_its_successor() -> None:
    Invoice.objects.create(number="A-1")
    base = _target()
    registry = SpecRegistry()
    registry.register("cancel_order", base)
    registry = registry.replace("cancel_order", _target())
    server = fresh_server()
    server.register_tools(registry)
    server.register_tool(
        order_selector_spec(SelectorKind.RETRIEVE, affordances={"cancel": base}),
        name="get_order",
    )

    assert _call(server, "get_order")["structuredContent"]["affordances"]["cancel"]["tool"] == (
        "cancel_order"
    )


class TestBindingOf:
    def test_a_spec_under_two_tool_names_has_no_one_name(self) -> None:
        cancel = _target()
        server = fresh_server()
        server.register_tool(cancel, name="cancel_order")
        server.register_tool(cancel, name="cancel_order_again")

        with pytest.raises(ValueError, match="cancel_order_again"):
            server.tools.binding_of(cancel)

    def test_an_empty_registry_holds_nothing(self) -> None:
        assert ToolRegistry().binding_of(_target()) is None
