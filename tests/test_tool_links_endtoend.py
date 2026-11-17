from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, override_settings
from django_service_specs import (
    Affordance,
    DataclassPresenter,
    FieldMarking,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceSpec,
    SpecLink,
    SpecRegistry,
    Unrestricted,
    ValueFormatter,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.testing import assert_tool_result_conforms
from tests.kernel_client import KernelClient
from tests.testapp.affordances import fresh_server, order_selector_spec
from tests.testapp.models import Invoice
from tests.testapp.urlconf_for import urlconf_for

OPEN = [Unrestricted()]
_UPPER = ValueFormatter(render=lambda value: str(value).upper(), produces="string")
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


def _met() -> bool:
    return True


_MET = [Affordance(code="closed", reason="Closed.", when=_met)]


def _equal_pair() -> tuple[ServiceSpec, ServiceSpec]:
    def build() -> ServiceSpec:
        return ServiceSpec(
            service=_void, permissions=OPEN, reads=Parameters((NUMBER, NOTE)), affordances=_MET
        )

    return build(), build()


class _Source:
    def __init__(self, spec: ServiceSpec) -> None:
        self.spec = spec

    def as_kernel_spec(self) -> ServiceSpec:
        return self.spec


@dataclass
class Tagged:
    number: str
    affordances: list[str]


@dataclass
class Meta:
    affordances: list[str]


@dataclass
class Nested:
    number: str
    meta: Meta


def _invoice_with_meta() -> Invoice:
    invoice = Invoice.objects.get()
    invoice.meta = Meta(affordances=["x"])
    return invoice


@dataclass
class _Stamped:
    number: Annotated[str, FieldMarking.formatted(_UPPER)]


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
        registered, linked = _equal_pair()
        assert registered == linked
        assert registered is not linked
        server = fresh_server()
        server.register_tool(registered, name="cancel_order")
        server.register_tool(
            order_selector_spec(SelectorKind.RETRIEVE, affordances={"cancel": linked}),
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


@pytest.mark.django_db
class TestWhatALinkLeavesAlone:
    def test_an_output_field_named_affordances_is_served_as_it_was(self) -> None:
        server = fresh_server()
        server.register_tool(
            ServiceSpec(
                service=lambda: Tagged(number="A-1", affordances=["x"]),
                permissions=OPEN,
                presenter=DataclassPresenter(Tagged),
            ),
            name="tagged",
        )
        listing: Any = server.list_tools(user=None)

        result = _call(server, "tagged")

        assert result["structuredContent"] == {"number": "A-1", "affordances": ["x"]}
        (tool,) = listing["tools"]
        assert tool["outputSchema"]["properties"]["affordances"]["type"] == "array"

    def test_a_nested_field_named_affordances_is_left_alone(self) -> None:
        Invoice.objects.create(number="A-1")
        cancel = _target()
        server = fresh_server()
        server.register_tool(cancel, name="cancel_order")
        server.register_tool(
            order_selector_spec(
                SelectorKind.RETRIEVE,
                selector=_invoice_with_meta,
                presenter=DataclassPresenter(Nested),
                affordances={"cancel": cancel},
            ),
            name="get_order",
        )
        listing: Any = server.list_tools(user=None)

        structured = _call(server, "get_order")["structuredContent"]

        assert structured["meta"] == {"affordances": ["x"]}
        assert structured["affordances"]["cancel"]["tool"] == "cancel_order"
        tool = next(entry for entry in listing["tools"] if entry["name"] == "get_order")
        meta = tool["outputSchema"]["properties"]["meta"]["properties"]["affordances"]
        assert meta["type"] == "array"

    def test_a_target_registered_as_two_tools_names_none_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        Invoice.objects.create(number="A-1")
        cancel = _target()
        server = fresh_server()
        server.register_tool(cancel, name="cancel_order")
        server.register_tool(cancel, name="cancel_order_again")
        server.register_tool(
            order_selector_spec(SelectorKind.RETRIEVE, affordances={"cancel": cancel}),
            name="get_order",
        )

        with caplog.at_level("WARNING"):
            entry = _call(server, "get_order")["structuredContent"]["affordances"]["cancel"]

        assert entry == {"available": True}
        assert "cancel_order_again" in caplog.text

    def test_a_link_binds_the_raw_value_a_formatter_renders_for_the_model(self) -> None:
        Invoice.objects.create(number="a-1")
        cancel = _target()
        server = fresh_server()
        server.register_tool(cancel, name="cancel_order")
        server.register_tool(
            order_selector_spec(
                SelectorKind.RETRIEVE,
                presenter=DataclassPresenter(_Stamped),
                affordances={"cancel": cancel},
            ),
            name="get_order",
        )

        structured = _call(server, "get_order")["structuredContent"]

        assert structured["number"] == "A-1"
        assert structured["affordances"]["cancel"]["arguments"] == {"number": "a-1"}


@pytest.mark.django_db
class TestADisplacedSpecSource:
    def test_a_displaced_spec_source_still_names_the_tool(self) -> None:
        Invoice.objects.create(number="A-1")
        base = _target()
        registry = SpecRegistry()
        registry.register("cancel_order", _Source(base))
        registry = registry.replace("cancel_order", _target())
        server = fresh_server()
        server.register_tools(registry)
        server.register_tool(
            order_selector_spec(SelectorKind.RETRIEVE, affordances={"cancel": base}),
            name="get_order",
        )

        entry = _call(server, "get_order")["structuredContent"]["affordances"]["cancel"]
        assert entry["tool"] == "cancel_order"


@pytest.mark.django_db
def test_a_served_tools_call_carries_the_link(client: Client) -> None:
    Invoice.objects.create(number="A-1")
    cancel = _target()
    server = fresh_server()
    server.register_tool(cancel, name="cancel_order")
    server.register_tool(
        order_selector_spec(SelectorKind.RETRIEVE, affordances={"cancel": cancel}),
        name="get_order",
    )
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        caller = KernelClient(client)
        caller.initialize()
        body = caller.call("get_order")

    assert body["result"]["structuredContent"]["affordances"]["cancel"] == {
        "available": True,
        "tool": "cancel_order",
        "arguments": {"number": "A-1"},
    }


@pytest.mark.django_db
def test_a_link_that_cannot_bind_fails_the_call_naming_it() -> None:
    Invoice.objects.create(number="A-1")
    by_pk = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda **_kwargs: None,
        reads=Parameters((Parameter("pk", "integer", required=True),)),
    )
    unbindable = ServiceSpec(service=_void, permissions=OPEN, instance_selector_spec=by_pk)
    server = fresh_server()
    server.register_tool(
        order_selector_spec(SelectorKind.RETRIEVE, affordances={"refund": unbindable}),
        name="get_order",
    )

    listing: Any = server.list_tools(user=None)

    (tool,) = listing["tools"]
    answer = tool["outputSchema"]["properties"]["affordances"]["properties"]["refund"]
    assert "tool" not in answer["properties"]
    with pytest.raises(ImproperlyConfigured, match="refund"):
        server.call_tool("get_order", {}, user=None)


@pytest.mark.django_db
def test_a_read_that_found_nothing_is_served_empty() -> None:
    cancel = _target()
    server = fresh_server()
    server.register_tool(cancel, name="cancel_order")
    server.register_tool(
        order_selector_spec(
            SelectorKind.RETRIEVE,
            selector=lambda: Invoice.objects.filter(number="none").first(),
            allow_none=True,
            affordances={"cancel": cancel},
        ),
        name="get_order",
    )

    assert _call(server, "get_order")["structuredContent"] == {}
