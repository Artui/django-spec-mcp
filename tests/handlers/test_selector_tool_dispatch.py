"""End-to-end kernel selector-tool dispatch: filter, then paginate, then present.

The original's pipeline questions, asked of kernel ``SelectorSpec`` tools
registered with ``register_tool`` and called through ``handle_tools_call``
and ``handle_tools_call_async`` alike: a ``filter_set`` advertises its filters
and narrows the rows, ``paginate=True`` lays one page out through the kernel's
``paginate_output`` with the untyped ``page`` / ``limit`` read as ints, a
declared read is shape-checked, a list return pages in memory, ``None`` under
``allow_none`` is the empty list and without it an internal error, and the spec's permissions, the rate limits,
a ``ServiceValidationError`` and a ``ServiceError`` are each answered on the
tool result or the JSON-RPC error the drf-services path answers with. The
async-only questions are in ``test_selector_tool_dispatch_async.py``.

Not carried, each for want of a kernel counterpart:

- a spec with ``selector=None`` refused at registration: a kernel
  ``SelectorSpec`` cannot be built without one;
- the ``selector_tool`` decorator, which builds a drf-services spec;
- an ``input_serializer`` validating a selector's arguments: a kernel selector
  takes the ``reads`` it declares, and their shape check is asked here;
- the object-level check on the resolved row, asked of every route in
  ``test_a_denied_caller_learns_nothing_of_the_target.py``.

The original is kept beside this file as ``test_selector_tool_dispatch_drfs.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import django_filters
import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django.test import Client, override_settings
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceError,
    ServiceValidationError,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from tests.kernel_client import KernelClient
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.kernel_shapes import InvoiceOutput
from tests.testapp.models import Invoice
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import tool_error

pytestmark = pytest.mark.django_db(transaction=True)

OPEN = [Unrestricted()]
routes = pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])


class InvoiceFilterSet(django_filters.FilterSet):
    sent = django_filters.BooleanFilter()
    min_amount = django_filters.NumberFilter(field_name="amount_cents", lookup_expr="gte")

    class Meta:
        model = Invoice
        fields = ["sent"]


def _list_invoices() -> Any:
    """Returns a raw, unscoped queryset: the spec's filter set narrows it."""
    return Invoice.objects.order_by("pk")


def _invoices(
    selector: Callable[..., Any] = _list_invoices,
    *,
    kind: SelectorKind = SelectorKind.LIST,
    **spec: Any,
) -> SelectorSpec:
    return SelectorSpec(
        kind=kind,
        selector=selector,
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=spec.pop("permissions", OPEN),
        **spec,
    )


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
        config=server.config,
    )
    if is_async:
        return await handle_tools_call_async(params, context)
    return await sync_to_async(handle_tools_call)(params, context)


async def _seed(count: int, **fields: Any) -> None:
    for n in range(count):
        await Invoice.objects.acreate(number=f"N{n}", amount_cents=100 * n, **fields)


def _numbers(out: Any) -> list[str]:
    assert not out.get("isError"), out
    payload = out["structuredContent"]
    rows = payload["items"] if isinstance(payload, dict) else payload
    return [row["number"] for row in rows]


# ---------- registration + tools/list ----------


def test_register_tool_creates_a_selector_binding() -> None:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)

    binding = server.register_tool(_invoices(), name="invoices.list")

    assert isinstance(binding, KernelToolBinding)
    assert binding.is_selector
    assert server.tools.get("invoices.list") is binding


def test_tools_list_emits_the_filter_sets_filters_in_the_input_schema() -> None:
    server = _server(_invoices(filter_set=InvoiceFilterSet))

    listed: Any = server.list_tools(user=None)
    schema = listed["tools"][0]["inputSchema"]

    assert set(schema["properties"]) == {"sent", "min_amount"}
    assert "required" not in schema


def test_a_paginated_tool_advertises_its_page_and_limit() -> None:
    listed: Any = _server(_invoices(), paginate=True).list_tools(user=None)

    assert {"page", "limit"} <= set(listed["tools"][0]["inputSchema"]["properties"])


# ---------- filtering ----------


@routes
async def test_a_filter_narrows_the_rows(is_async: bool) -> None:
    await _seed(2, sent=True)
    await Invoice.objects.acreate(number="unsent", amount_cents=900, sent=False)
    server = _server(_invoices(filter_set=InvoiceFilterSet))

    assert _numbers(await _call(server, {"sent": True}, is_async=is_async)) == ["N0", "N1"]
    assert _numbers(await _call(server, {"min_amount": 100}, is_async=is_async)) == [
        "N1",
        "unsent",
    ]
    # With no filter argument the filter set narrows nothing.
    assert len(_numbers(await _call(server, {}, is_async=is_async))) == 3


@routes
async def test_with_no_filter_set_an_argument_is_refused_not_ignored(is_async: bool) -> None:
    # Changed by design: a drf-services selector without a filter set ignored a
    # filter-shaped argument. A kernel spec's argument set is closed, so one it
    # does not declare is refused rather than silently dropped.
    await _seed(2)

    out = await _call(_server(_invoices()), {"sent": True}, is_async=is_async)

    assert tool_error(out)["type"] == "validation_error"


@routes
async def test_a_retrieve_applies_the_filter_set_before_taking_its_row(is_async: bool) -> None:
    await Invoice.objects.acreate(number="unsent", amount_cents=1, sent=False)
    await Invoice.objects.acreate(number="sent", amount_cents=2, sent=True)
    server = _server(_invoices(kind=SelectorKind.RETRIEVE, filter_set=InvoiceFilterSet))

    out = await _call(server, {"sent": True}, is_async=is_async)

    assert out["structuredContent"]["number"] == "sent"


# ---------- pagination ----------


@routes
async def test_pagination_wraps_the_page_in_its_envelope(is_async: bool) -> None:
    await _seed(7)

    out = await _call(
        _server(_invoices(), paginate=True), {"page": 2, "limit": 3}, is_async=is_async
    )

    payload = out["structuredContent"]
    assert [row["number"] for row in payload["items"]] == ["N3", "N4", "N5"]
    assert (payload["page"], payload["totalPages"], payload["hasNext"]) == (2, 3, True)


@routes
async def test_the_last_page_reports_no_next(is_async: bool) -> None:
    await _seed(2)

    out = await _call(
        _server(_invoices(), paginate=True), {"page": 1, "limit": 100}, is_async=is_async
    )

    assert (out["structuredContent"]["totalPages"], out["structuredContent"]["hasNext"]) == (
        1,
        False,
    )


@routes
@pytest.mark.parametrize(
    ("arguments", "page", "rows"),
    [
        pytest.param({}, 1, 3, id="defaults"),
        pytest.param({"page": True, "limit": "lol"}, 1, 3, id="unreadable"),
        pytest.param({"page": "2", "limit": "2"}, 2, 1, id="numeric-strings"),
    ],
)
async def test_page_and_limit_are_read_as_ints(
    arguments: dict[str, Any], page: int, rows: int, is_async: bool
) -> None:
    # A bool is not read as ``1`` and an unreadable value falls back, to the
    # first page and the kernel's default size, rather than being refused.
    await _seed(3)

    out = await _call(_server(_invoices(), paginate=True), arguments, is_async=is_async)

    assert out["structuredContent"]["page"] == page
    assert len(out["structuredContent"]["items"]) == rows


@routes
async def test_the_full_pipeline_filters_then_paginates(is_async: bool) -> None:
    await _seed(5, sent=True)
    await _seed(3, sent=False)
    server = _server(_invoices(filter_set=InvoiceFilterSet), paginate=True)

    out = await _call(server, {"sent": True, "page": 2, "limit": 2}, is_async=is_async)

    payload = out["structuredContent"]
    assert [row["number"] for row in payload["items"]] == ["N2", "N3"]
    assert (payload["totalPages"], payload["hasNext"]) == (3, True)


@routes
async def test_a_list_return_is_paged_in_memory(is_async: bool) -> None:
    rows = [InvoiceOutput(id=n, number=f"L{n}", amount_cents=0, sent=False) for n in range(5)]
    server = _server(_invoices(lambda: rows), paginate=True)

    out = await _call(server, {"page": 2, "limit": 2}, is_async=is_async)

    assert [row["number"] for row in out["structuredContent"]["items"]] == ["L2", "L3"]


@routes
async def test_paging_a_return_that_is_not_sized_is_refused_by_name(is_async: bool) -> None:
    def _generator() -> Any:
        yield from ()

    server = _server(_invoices(_generator), paginate=True)

    with pytest.raises(TypeError, match="sized"):
        await _call(server, {}, is_async=is_async)


# ---------- reads, returns and the result ----------


@routes
async def test_a_declared_read_is_shape_checked(is_async: bool) -> None:
    seen: list[Any] = []

    def selector(*, token: str) -> list[Any]:
        seen.append(token)
        return []

    server = _server(
        _invoices(selector, reads=Parameters([Parameter("token", "string", required=True)]))
    )

    accepted = await _call(server, {"token": "abc"}, is_async=is_async)
    missing = await _call(server, {}, is_async=is_async)
    mistyped = await _call(server, {"token": 7}, is_async=is_async)

    assert _numbers(accepted) == []
    assert tool_error(missing)["type"] == "validation_error"
    assert tool_error(mistyped)["type"] == "validation_error"
    assert seen == ["abc"]


@routes
async def test_none_under_allow_none_is_the_empty_list(is_async: bool) -> None:
    # Changed by design: a drf-services ``LIST`` returning ``None`` was served
    # as ``{}`` with nothing declared. A kernel spec says ``None`` is an answer
    # by declaring ``allow_none=True``; without it the kernel's ``present``
    # raises ``TypeError`` and the call is an internal error
    # (test_none_without_allow_none_is_an_internal_error).
    out = await _call(_server(_invoices(lambda: None, allow_none=True)), {}, is_async=is_async)

    assert out["structuredContent"] == []


@routes
async def test_none_without_allow_none_raises_in_process(is_async: bool) -> None:
    with pytest.raises(TypeError):
        await _call(_server(_invoices(lambda: None)), {}, is_async=is_async)


@routes
def test_none_without_allow_none_is_an_internal_error(is_async: bool, client: Client) -> None:
    """What a ``LIST`` returning ``None`` with nothing declared answers on the wire,
    on both mounts: the transport's internal error, never an empty result."""
    server = MCPServer(name="t", auth_backend=HeaderAuthBackend())
    server.register_tool(_invoices(lambda: None), name="invoices.list")
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        caller = KernelClient(client)
        caller.initialize()
        response = caller.post("tools/call", {"name": "invoices.list", "arguments": {}})

    assert response.status_code == 500
    assert response.json()["error"] == {"code": -32603, "message": "Internal error"}


@routes
async def test_a_kwargs_provider_merges_into_the_selectors_pool(is_async: bool) -> None:
    seen: list[str] = []

    def selector(*, scope: str) -> list[Any]:
        seen.append(scope)
        return []

    server = _server(
        _invoices(
            selector,
            reads=Parameters([Parameter("scope", "string", required=True)]),
            kwargs=lambda: {"scope": "from-provider"},
        )
    )

    await _call(server, {}, is_async=is_async)

    assert seen == ["from-provider"]


@routes
async def test_the_include_structured_content_override_is_honoured(is_async: bool) -> None:
    await _seed(1)
    server = _server(_invoices(), include_structured_content=False, include_output_schema=False)

    out = await _call(server, {}, is_async=is_async)

    assert "structuredContent" not in out
    # The text payload still carries the full result.
    assert "N0" in out["content"][0]["text"]


@routes
async def test_a_retrieve_presents_its_one_row(is_async: bool) -> None:
    await _seed(1)
    server = _server(_invoices(lambda: Invoice.objects.get(), kind=SelectorKind.RETRIEVE))

    out = await _call(server, {}, is_async=is_async)

    assert out["structuredContent"]["number"] == "N0"


@routes
async def test_a_retrieve_without_a_presenter_serves_its_value_as_it_is(is_async: bool) -> None:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: {"id": 1}, permissions=OPEN),
        name="invoices.list",
    )

    out = await _call(server, {}, is_async=is_async)

    assert out["structuredContent"] == {"id": 1}


# ---------- refusals ----------


class _Deny(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:
        return False


class _AlwaysLimited:
    def consume(self, request: Any, token: Any) -> int | None:
        return 30


@routes
async def test_a_denied_caller_is_insufficient_permission(is_async: bool) -> None:
    out = await _call(_server(_invoices(permissions=[_Deny()])), {}, is_async=is_async)

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN


@routes
async def test_a_rate_limited_call_is_refused_with_its_retry_after(is_async: bool) -> None:
    out = await _call(_server(_invoices(), rate_limits=[_AlwaysLimited()]), {}, is_async=is_async)

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.RATE_LIMITED
    assert out.data == {"retryAfter": 30}


def _raises(exc: Exception) -> Callable[[], Any]:
    def selector() -> Any:
        raise exc

    return selector


@routes
@pytest.mark.parametrize("record", [False, True], ids=["not-recorded", "recorded"])
@pytest.mark.parametrize(
    ("exc", "error_type"),
    [
        pytest.param(
            ServiceValidationError({"field": ["bad"]}), "validation_error", id="validation"
        ),
        pytest.param(ServiceError("oh no"), "service_error", id="service"),
    ],
)
async def test_a_selectors_service_error_is_a_tool_error(
    exc: Exception, error_type: str, record: bool, is_async: bool
) -> None:
    # Answered alike whether or not the span records it.
    with override_settings(SPEC_MCP={"RECORD_SERVICE_EXCEPTIONS": record}):
        out = await _call(_server(_invoices(_raises(exc))), {}, is_async=is_async)

    assert tool_error(out)["type"] == error_type
