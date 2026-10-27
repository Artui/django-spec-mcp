"""Kernel ``RETRIEVE`` selector tools: a missing row, and ``allow_none``.

The original's questions, asked of a kernel ``SelectorSpec`` registered with
``register_tool``, whose ``DataclassPresenter`` renders an ``Invoice`` row: a
missing row (``None``, an empty queryset, ``Http404`` or ``DoesNotExist``) is a
tool-level ``not_found`` worded as the drf-services path words it; under
``allow_none`` it is the empty result instead; a queryset is materialized to
its row; and a ``LIST`` has no row to miss, so ``allow_none`` does not turn
its ``Http404`` into an empty result and its ``DoesNotExist`` is the author's
error. Each on the sync and the async handler. The original is kept beside
this file as ``test_retrieve_tool_missing_drfs.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.db.models import QuerySet
from django.http import Http404, HttpRequest
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from tests.testapp.kernel_shapes import InvoiceOutput
from tests.testapp.models import Invoice
from tests.utils import tool_error

pytestmark = pytest.mark.django_db(transaction=True)

_PK = Parameters([Parameter("pk", "integer", required=True)])


def _get_invoice(*, pk: int) -> Invoice | None:
    return Invoice.objects.filter(pk=pk).first()


def _invoice_queryset(*, pk: int) -> QuerySet[Invoice]:
    return Invoice.objects.filter(pk=pk)


def _strict_get(*, pk: int) -> Invoice:
    return Invoice.objects.get(pk=pk)


def _get_or_404(*, pk: int) -> Invoice:
    """``get_object_or_404``'s idiom; ``Http404`` is not an ``ObjectDoesNotExist``."""
    raise Http404(f"no invoice {pk}")


def _server(
    selector: Callable[..., Any], *, kind: SelectorKind = SelectorKind.RETRIEVE, **spec: Any
) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(
        SelectorSpec(
            kind=kind,
            selector=selector,
            reads=_PK if kind is SelectorKind.RETRIEVE else Parameters(),
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
            **spec,
        ),
        name="invoices.get",
    )
    return server


async def _call(server: MCPServer, arguments: dict[str, Any], *, is_async: bool) -> Any:
    params: dict[str, Any] = {"name": "invoices.get", "arguments": arguments}
    context = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )
    if is_async:
        return await handle_tools_call_async(params, context)
    return await sync_to_async(handle_tools_call)(params, context)


_MISSING = {
    "type": "not_found",
    "message": "invoices.get: no matching instance found",
}

routes = pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
missing_rows = pytest.mark.parametrize(
    "selector",
    [_get_invoice, _invoice_queryset, _get_or_404, _strict_get],
    ids=["none", "empty-queryset", "http404", "does-not-exist"],
)


@routes
@missing_rows
async def test_a_missing_row_is_not_found_by_default(
    selector: Callable[..., Any], is_async: bool
) -> None:
    out = await _call(_server(selector), {"pk": 99999}, is_async=is_async)

    assert tool_error(out) == _MISSING


@routes
@missing_rows
async def test_allow_none_returns_an_empty_result(
    selector: Callable[..., Any], is_async: bool
) -> None:
    out = await _call(_server(selector, allow_none=True), {"pk": 99999}, is_async=is_async)

    assert "isError" not in out
    # Nothing to present is a value, and MCP requires structured content to be
    # an object, so it is ``{}`` rather than absent or ``null``.
    assert out["structuredContent"] == {}
    assert out["content"][0]["text"] == "{}"


@routes
@pytest.mark.parametrize("selector", [_get_invoice, _invoice_queryset, _strict_get])
async def test_a_found_row_is_presented(selector: Callable[..., Any], is_async: bool) -> None:
    # A queryset is materialized to its one row rather than presented as a set.
    invoice = await Invoice.objects.acreate(number="n-1", amount_cents=5)

    out = await _call(_server(selector), {"pk": invoice.pk}, is_async=is_async)

    assert "isError" not in out
    assert out["structuredContent"] == {
        "id": invoice.pk,
        "number": "n-1",
        "amount_cents": 5,
        "sent": False,
    }


@routes
@pytest.mark.parametrize("allow_none", [False, True], ids=["default", "allow_none"])
async def test_a_list_selectors_http404_is_not_found_even_with_allow_none(
    allow_none: bool, is_async: bool
) -> None:
    # ``allow_none`` speaks for a missing row, and a LIST has none to miss.
    def missing_parent() -> QuerySet[Invoice]:
        raise Http404("no parent")

    server = _server(missing_parent, kind=SelectorKind.LIST, allow_none=allow_none)

    out = await _call(server, {}, is_async=is_async)

    assert tool_error(out) == {"type": "not_found", "message": "invoices.get: not found"}


@routes
async def test_a_list_selectors_does_not_exist_propagates(is_async: bool) -> None:
    # A LIST has no row whose absence it could report, so a lookup failing
    # inside it is the author's error rather than an empty list.
    def strict_parent() -> QuerySet[Invoice]:
        return Invoice.objects.filter(pk=Invoice.objects.get(pk=99999).pk)

    with pytest.raises(Invoice.DoesNotExist):
        await _call(_server(strict_parent, kind=SelectorKind.LIST), {}, is_async=is_async)
