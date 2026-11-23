from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)
from typing_extensions import TypedDict

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from tests.testapp.kernel_shapes import InvoiceOutput
from tests.testapp.models import Invoice
from tests.utils import make_server

ROUTES = ["handler", "async_handler", "call_tool", "acall_tool"]


@dataclass
class _Close:
    ticket: int
    tenant: int


@dataclass
class _Closed:
    ticket: int
    data_tenant: int
    service_tenant: int


class _TenantScope(TypedDict):
    tenant: int


def _scope() -> _TenantScope:
    return {"tenant": 999}


def _close(*, ticket: int, tenant: int, data: _Close) -> _Closed:
    return _Closed(ticket=ticket, data_tenant=data.tenant, service_tenant=tenant)


@dataclass
class _Line:
    number: str
    customer: int


@dataclass
class _Recorded:
    number: str
    row_customer: int
    service_customer: int


class _CustomerScope(TypedDict):
    customer: int


def _customer_scope() -> _CustomerScope:
    return {"customer": 999}


def _record_lines(*, customer: int, data: list[_Line]) -> list[_Recorded]:
    return [
        _Recorded(number=row.number, row_customer=row.customer, service_customer=customer)
        for row in data
    ]


def _server() -> MCPServer:
    server = make_server()
    server.register_tool(
        ServiceSpec(
            service=_close,
            validator=DataclassValidator(_Close),
            presenter=DataclassPresenter(_Closed),
            kwargs=_scope,
            atomic=False,
            permissions=[Unrestricted()],
        ),
        name="close",
        description="Close a ticket.",
    )
    server.register_tool(
        ServiceSpec(
            service=_record_lines,
            validator=DataclassValidator(_Line),
            presenter=DataclassPresenter(_Recorded),
            kwargs=_customer_scope,
            many=True,
            atomic=False,
            permissions=[Unrestricted()],
        ),
        name="record",
        description="Record lines.",
    )
    return server


def _input_schema(server: MCPServer, name: str) -> dict[str, Any]:
    listed: Any = server.list_tools(user=None)
    (tool,) = [tool for tool in listed["tools"] if tool["name"] == name]
    return tool["inputSchema"]


async def _served(server: MCPServer, route: str, name: str, arguments: dict[str, Any]) -> Any:
    if route == "call_tool":
        out: Any = (await sync_to_async(server.call_tool)(name, arguments, user=None)).to_dict()
    elif route == "acall_tool":
        out = await server.acall_tool(name, arguments, user=None)
    else:
        context = MCPCallContext(
            http_request=HttpRequest(),
            token=TokenInfo(user=None),
            tools=server.tools,
            resources=server.resources,
            prompts=server.prompts,
            protocol_version="2025-11-25",
        )
        params = {"name": name, "arguments": arguments}
        out = await (
            handle_tools_call_async(params, context)
            if route == "async_handler"
            else sync_to_async(handle_tools_call)(params, context)
        )
    assert isinstance(out, dict)
    assert out.get("isError") is not True, out
    return out["structuredContent"]


def test_a_validator_field_named_like_a_typed_kwargs_key_is_asked_of_the_caller() -> None:
    schema = _input_schema(_server(), "close")

    assert set(schema["properties"]) == {"ticket", "tenant"}
    assert set(schema["required"]) == {"ticket", "tenant"}


def test_a_many_row_field_named_like_a_typed_kwargs_key_stays_in_each_item() -> None:
    rows = _input_schema(_server(), "record")["properties"]["items"]["items"]

    assert set(rows["properties"]) == {"number", "customer"}
    assert set(rows["required"]) == {"number", "customer"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", ROUTES)
async def test_the_service_keyword_is_the_providers_and_data_the_callers(route: str) -> None:
    out = await _served(_server(), route, "close", {"ticket": 1, "tenant": 5})

    assert out == {"ticket": 1, "data_tenant": 5, "service_tenant": 999}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", ROUTES)
async def test_each_row_keeps_the_callers_value_and_the_service_the_providers(
    route: str,
) -> None:
    arguments = {"items": [{"number": "A", "customer": 5}, {"number": "B", "customer": 6}]}

    out = await _served(_server(), route, "record", arguments)

    assert out["items"] == [
        {"number": "A", "row_customer": 5, "service_customer": 999},
        {"number": "B", "row_customer": 6, "service_customer": 999},
    ]


@dataclass
class _Rename:
    number: str


class _LookupScope(TypedDict):
    number: str
    note: str


def _lookup_scope() -> _LookupScope:
    return {"number": "SRV", "note": "SRV"}


def _rename(*, instance: Invoice, number: str, note: str) -> Invoice:
    instance.number = f"{number}/{note}"
    instance.save(update_fields=["number"])
    return instance


def _lookup_server(seen: list[tuple[str, str]]) -> MCPServer:
    def by_pk(*, pk: int, number: str, note: str) -> Any:
        seen.append((number, note))
        return Invoice.objects.filter(pk=pk)

    server = make_server()
    server.register_tool(
        ServiceSpec(
            service=_rename,
            validator=DataclassValidator(_Rename),
            presenter=DataclassPresenter(InvoiceOutput),
            reads=Parameters([Parameter("note", "string", required=True)]),
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=by_pk,
                reads=Parameters([Parameter("pk", "integer", required=True)]),
                kwargs=_lookup_scope,
            ),
            atomic=False,
            permissions=[Unrestricted()],
        ),
        name="rename",
        description="Rename an invoice.",
    )
    return server


def test_a_target_lookups_kwargs_namesakes_are_asked_of_the_caller() -> None:
    schema = _input_schema(_lookup_server([]), "rename")

    assert set(schema["properties"]) == {"pk", "number", "note"}
    assert set(schema["required"]) == {"pk", "number", "note"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", ROUTES)
async def test_a_target_lookups_kwargs_reach_the_selector_and_the_service_the_callers(
    route: str,
) -> None:
    invoice = await Invoice.objects.acreate(number="OLD", amount_cents=1)
    seen: list[tuple[str, str]] = []
    arguments = {"pk": invoice.pk, "number": "CALLER", "note": "SENT"}

    out = await _served(_lookup_server(seen), route, "rename", arguments)

    assert out["number"] == "CALLER/SENT"
    assert seen == [("SRV", "SRV")]
