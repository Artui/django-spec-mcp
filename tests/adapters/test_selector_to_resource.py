"""A kernel resource carries every field of its spec, because the read dispatches it.

``register_resource(spec, ...)`` with a kernel ``RETRIEVE`` ``SelectorSpec``
reads through the kernel's ``dispatch``, so what the spec declares runs on
``resources/read`` as it runs on every other transport: a precondition gates the
read, the presenter renders the row and queryset shaping is applied. Asked here:
a spec declaring any of those registers, and a read runs its precondition and
its presenter.

Nothing the spec declares is dropped, so there is nothing to refuse at
registration: dropping a precondition silently would look like success.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceError,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import resource_not_found_code
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from tests.testapp.kernel_shapes import InvoiceOutput
from tests.testapp.models import Invoice


def _invoice(*, pk: int) -> Any:
    return Invoice.objects.filter(pk=pk)


def _spec(**declared: Any) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_invoice,
        permissions=[Unrestricted()],
        reads=Parameters([Parameter("pk", "integer", required=True)]),
        presenter=DataclassPresenter(InvoiceOutput),
        **declared,
    )


def _server(spec: SelectorSpec) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_resource(spec, name="invoice", uri_template="invoices://{pk}")
    return server


def _read(server: MCPServer, uri: str) -> Any:
    context = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )
    return handle_resources_read({"uri": uri}, context)


def _closed(**_: Any) -> None:
    raise ServiceError("Invoices are closed for the night.")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preconditions", [_closed]),
        ("extend_queryset", lambda queryset: queryset),
        ("allow_none", True),
        ("metadata", {"k": "v"}),
    ],
)
def test_every_declared_spec_field_registers_and_is_kept(field: str, value: Any) -> None:
    spec = _spec(**{field: value})
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)

    binding: Any = server.register_resource(spec, name="invoice", uri_template="invoices://{pk}")

    assert binding.spec is spec


@pytest.mark.django_db
def test_a_read_runs_the_specs_precondition() -> None:
    # That the precondition ran, with the URI's variable in its pool, is this
    # module's question. How a refusal from it is answered belongs to the read
    # path, so the precondition here records and lets the read through.
    seen: list[Any] = []

    def _seen(*, pk: int) -> None:
        seen.append(pk)

    invoice = Invoice.objects.create(number="INV-1")
    server = _server(_spec(preconditions=[_seen]))

    _read(server, f"invoices://{invoice.pk}")

    assert seen == [invoice.pk]


@pytest.mark.django_db
def test_a_read_applies_the_specs_queryset_shaping() -> None:
    # ``extend_queryset`` narrowing the row away makes the read a miss, which
    # only a read that shapes the selector's queryset can answer.
    invoice = Invoice.objects.create(number="INV-1")
    server = _server(_spec(extend_queryset=lambda queryset: queryset.none()))

    out = _read(server, f"invoices://{invoice.pk}")

    assert isinstance(out, JsonRpcError)
    assert out.code == resource_not_found_code("2025-11-25")


@pytest.mark.django_db
def test_a_read_renders_the_row_through_the_specs_presenter() -> None:
    invoice = Invoice.objects.create(number="INV-1", amount_cents=250)

    out = _read(_server(_spec()), f"invoices://{invoice.pk}")

    assert '"number": "INV-1"' in out["contents"][0]["text"]
    assert '"amount_cents": 250' in out["contents"][0]["text"]
