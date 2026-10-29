"""A kernel service tool's bulk write and object-level permission, over ``tools/call``.

Asks of kernel specs registered with ``register_tool``: a collection selector
narrowed by a FilterSet hands the service the whole collection to write, and a
permission's ``has_object_permission`` judges the row an instance selector
resolved, refusing the call before the service runs. The kernel's ``dispatch`` owns both, so these hold that the
transport hands it the arguments they read and maps its refusal to ``-32006``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import django_filters
import pytest
from django.http import HttpRequest
from django_service_specs import (
    DataclassValidator,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from tests.testapp.models import Invoice

OPEN = [Unrestricted()]

pytestmark = pytest.mark.django_db


@dataclass
class _NumberInput:
    number: str


class _DenyObject(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:
        return True

    def has_object_permission(self, principal: Any, spec: Any, target: Any) -> bool:
        return False


class _UnsentFilterSet(django_filters.FilterSet):
    sent = django_filters.BooleanFilter()

    class Meta:
        model = Invoice
        fields = ["sent"]


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def test_a_collection_selector_hands_the_service_the_filtered_collection() -> None:
    Invoice.objects.create(number="A", amount_cents=1, sent=False)
    Invoice.objects.create(number="B", amount_cents=1, sent=False)
    Invoice.objects.create(number="C", amount_cents=1, sent=True)

    def _bulk_delete(*, collection: Any) -> dict[str, int]:
        n, _ = collection.delete()
        return {"deleted": n}

    server = _server()
    server.register_tool(
        ServiceSpec(
            service=_bulk_delete,
            collection_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST,
                selector=Invoice.objects.all,
                filter_set=_UnsentFilterSet,
                permissions=OPEN,
            ),
            permissions=OPEN,
            atomic=False,
        ),
        name="invoices.purge_unsent",
    )
    out = handle_tools_call(
        {"name": "invoices.purge_unsent", "arguments": {"sent": False}}, _ctx(server)
    )
    assert out.get("isError") is not True, out
    assert out["structuredContent"] == {"deleted": 2}
    assert list(Invoice.objects.values_list("number", flat=True)) == ["C"]


def test_an_object_permission_denies_the_resolved_row_before_the_service_runs() -> None:
    invoice = Invoice.objects.create(number="A", amount_cents=1)

    def _rename(*, instance: Invoice, number: str) -> Invoice:
        instance.number = number
        instance.save(update_fields=["number"])
        return instance

    server = _server()
    server.register_tool(
        ServiceSpec(
            service=_rename,
            validator=DataclassValidator(_NumberInput),
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda *, pk: Invoice.objects.filter(pk=pk),
                reads=Parameters([Parameter("pk", "integer", required=True)]),
                permissions=OPEN,
            ),
            permissions=[_DenyObject()],
            atomic=False,
        ),
        name="invoices.rename",
    )
    out = handle_tools_call(
        {"name": "invoices.rename", "arguments": {"pk": invoice.pk, "number": "B"}},
        _ctx(server),
    )
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN
    invoice.refresh_from_db()
    assert invoice.number == "A"
