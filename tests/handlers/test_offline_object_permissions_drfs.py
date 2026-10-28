"""Object-level permissions on the path that skips ``dispatch_spec``.

``resources/read`` calls ``run_selector`` directly rather than through
``dispatch_spec``, because its binding holds a bare callable and not the spec.
A spec's ``permission_classes`` used to be evaluated there for their
class-level half only — ``has_object_permission`` never ran anywhere in the
package. These tests hold the resolved-target guard on that path.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest
from django_service_specs import SelectorKind
from rest_framework.permissions import BasePermission
from rest_framework_services.types.selector_spec import SelectorSpec

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from tests.testapp.models import Invoice
from tests.testapp.serializers_drfs import InvoiceOutputSerializer


class IsAliceInvoice(BasePermission):
    """Class-level check passes for everyone; ownership lives per row.

    The exact shape the guard exists for: a spec whose authorization is
    expressed on the object, which over HTTP runs in
    ``check_object_permissions`` and off HTTP in ``enforce_permissions``.
    """

    message = "Not your invoice"

    def has_permission(self, request: Any, view: Any) -> bool:
        return True

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        return str(obj.number).startswith("alice")


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def _get_invoice(*, pk: str) -> Invoice:
    return Invoice.objects.get(pk=int(pk))


def _register_invoice_resource(server: MCPServer) -> None:
    server.register_resource(
        name="invoice",
        uri_template="invoices://{pk}",
        selector=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_get_invoice,
            output_serializer=InvoiceOutputSerializer,
            permission_classes=[IsAliceInvoice],
        ),
    )


# ---------- resources/read ----------


@pytest.mark.django_db
def test_resource_read_refuses_a_row_the_object_permission_denies() -> None:
    """The failure this guard exists for: the class-level check passes, the row
    belongs to someone else, and the body used to be serialised out anyway."""
    theirs = Invoice.objects.create(number="bob-1", amount_cents=100)
    server = _server()
    _register_invoice_resource(server)

    out = handle_resources_read({"uri": f"invoices://{theirs.pk}"}, _ctx(server))

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.django_db
def test_resource_read_returns_a_row_the_object_permission_allows() -> None:
    mine = Invoice.objects.create(number="alice-1", amount_cents=100)
    server = _server()
    _register_invoice_resource(server)

    out = handle_resources_read({"uri": f"invoices://{mine.pk}"}, _ctx(server))

    assert isinstance(out, dict)
    assert '"number": "alice-1"' in out["contents"][0]["text"]


@pytest.mark.django_db(transaction=True)
async def test_async_resource_read_refuses_the_same_row() -> None:
    from asgiref.sync import sync_to_async

    theirs = await sync_to_async(Invoice.objects.create)(number="bob-2", amount_cents=1)
    server = _server()
    _register_invoice_resource(server)

    out = await handle_resources_read_async({"uri": f"invoices://{theirs.pk}"}, _ctx(server))

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.django_db
def test_resource_read_with_no_drf_permissions_skips_the_guard() -> None:
    """A resource guarded only by a per-binding MCP permission has no
    object-level half to run, and must not be refused for the lack of one."""

    class _AlwaysAllow:
        def has_permission(self, *_args: object, **_kwargs: object) -> bool:
            return True

    mine = Invoice.objects.create(number="bob-3", amount_cents=1)
    server = _server()
    server.register_resource(
        name="invoice",
        uri_template="invoices://{pk}",
        selector=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_get_invoice,
            output_serializer=InvoiceOutputSerializer,
        ),
        permissions=[_AlwaysAllow()],
    )

    out = handle_resources_read({"uri": f"invoices://{mine.pk}"}, _ctx(server))

    assert isinstance(out, dict)


@pytest.mark.django_db
def test_resource_read_of_a_list_runs_only_the_class_level_check() -> None:
    """A ``LIST`` resource resolves a set, which is authorized per-set: the
    guard must not call ``has_object_permission`` with a queryset."""

    class _ExplodesOnObjects(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            return True

        def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
            raise AssertionError("object permissions must not run against a queryset")

    Invoice.objects.create(number="bob-4", amount_cents=1)
    server = _server()
    server.register_resource(
        name="invoices",
        uri_template="invoices://all",
        selector=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda: Invoice.objects.all(),
            output_serializer=InvoiceOutputSerializer,
            permission_classes=[_ExplodesOnObjects],
        ),
    )

    out = handle_resources_read({"uri": "invoices://all"}, _ctx(server))

    assert isinstance(out, dict)
