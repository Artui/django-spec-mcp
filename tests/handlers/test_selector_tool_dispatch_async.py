"""What only the async route asks of a kernel selector tool.

The original was the async twin of a subset of the sync pipeline tests; on the
kernel path every one of those runs on both handlers in
``test_selector_tool_dispatch.py``, so this file asks what is particular to an
async caller: an ``async def`` selector is awaited by ``adispatch`` and still
answers the sync handler, and a paginated call's presenter is handed the page
and not the whole set, which is the kernel form of the original's
output-context provider receiving the resolved page. The original is kept
beside this file as ``test_selector_tool_dispatch_async_drfs.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django_service_specs import DataclassPresenter, SelectorKind, SelectorSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from tests.testapp.kernel_shapes import InvoiceOutput
from tests.testapp.models import Invoice

pytestmark = pytest.mark.django_db(transaction=True)


def _server(spec: SelectorSpec, **tool: Any) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(spec, name="invoices.list", **tool)
    return server


async def _call(server: MCPServer, arguments: dict[str, Any], *, is_async: bool) -> Any:
    params: dict[str, Any] = {"name": "invoices.list", "arguments": arguments}
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


async def _list_invoices() -> list[Invoice]:
    return [invoice async for invoice in Invoice.objects.order_by("amount_cents")]


class _RecordingPresenter(DataclassPresenter):
    """Presents as ``DataclassPresenter`` does, recording each row it was handed."""

    def __init__(self) -> None:
        super().__init__(InvoiceOutput)
        self.handed: list[str] = []

    def present(self, value: Any) -> Any:
        self.handed.append(value.number)
        return super().present(value)


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_an_async_selector_answers_either_handler(is_async: bool) -> None:
    for n in range(3):
        await Invoice.objects.acreate(number=f"INV-{n}", amount_cents=n)
    server = _server(
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
        )
    )

    out = await _call(server, {}, is_async=is_async)

    assert [row["number"] for row in out["structuredContent"]] == ["INV-0", "INV-1", "INV-2"]


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_a_paginated_presenter_is_handed_the_page_alone(is_async: bool) -> None:
    for n in range(5):
        await Invoice.objects.acreate(number=f"INV-{n}", amount_cents=n)
    presenter = _RecordingPresenter()
    server = _server(
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda: Invoice.objects.order_by("amount_cents"),
            presenter=presenter,
            permissions=[Unrestricted()],
        ),
        paginate=True,
    )

    out = await _call(server, {"page": 2, "limit": 2}, is_async=is_async)

    assert [row["number"] for row in out["structuredContent"]["items"]] == ["INV-2", "INV-3"]
    assert presenter.handed == ["INV-2", "INV-3"]
