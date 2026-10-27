"""Outbound bounds on kernel tools and resources: page-size clamp, row and byte ceilings, deadline.

Asks of specs registered with ``register_tool`` / ``register_resource`` what the
original, kept beside this file as ``test_outbound_bounds_drfs.py``, asks of
drf-services bindings. ``max_page_size``, ``max_result_bytes`` and
``dispatch_timeout`` are per-tool knobs on ``register_tool``, each deferring to
the server's value when left ``UNSET`` and disabled by ``None``; the clamps and
the page envelope are the kernel's ``paginate_output``, handed the bound the
binding and the server resolve.

The prompt cases, ``run_with_deadline``, ``resolve_bound`` and
``build_mcp_config`` name no spec, and are asked here as the original asks them
so they outlive it. Registration-time coverage for the unpaginated-list warning
lives in ``tests/server/test_unbounded_list_warning.py``.
"""

from __future__ import annotations

import asyncio
import time
import warnings
from typing import Any

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.http import HttpRequest
from django_service_specs import (
    UNSET,
    DataclassPresenter,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.async_dispatch import adispatch
from django_spec_mcp.handlers.handle_prompts_get import handle_prompts_get
from django_spec_mcp.handlers.handle_prompts_get_async import handle_prompts_get_async
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.handle_tools_list import handle_tools_list
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import resolve_bound, run_with_deadline
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.types.prompt_binding import PromptBinding
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from tests.testapp.kernel_shapes import InvoiceOutput
from tests.testapp.models import Invoice
from tests.utils import tool_error

OPEN = [Unrestricted()]

# ---------- helpers ----------


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _ctx(server: MCPServer, config: MCPConfig | None = None) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        config=config if config is not None else build_mcp_config(),
    )


def _list_spec(selector: Any = Invoice.objects.all, **fields: Any) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=selector,
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=OPEN,
        **fields,
    )


def _register(
    server: MCPServer, spec: Any = None, *, name: str = "invoices.list", **knobs: Any
) -> None:
    """Register a LIST tool, silencing the unrelated registration warnings."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        server.register_tool(spec if spec is not None else _list_spec(), name=name, **knobs)


def _register_list(server: MCPServer, **knobs: Any) -> None:
    _register(server, paginate=True, **knobs)


def _seed(count: int) -> None:
    for i in range(count):
        Invoice.objects.create(number=f"INV-{i}", amount_cents=100 + i, sent=False)


def _call(server: MCPServer, arguments: dict[str, Any], config: MCPConfig | None = None) -> Any:
    return handle_tools_call(
        {"name": "invoices.list", "arguments": arguments}, _ctx(server, config)
    )


def _rows() -> list[dict[str, str]]:
    return [{"number": f"N-{i}"} for i in range(5)]


# ---------- page-size clamp ----------


@pytest.mark.django_db
def test_a_model_supplied_limit_is_clamped_to_the_server_ceiling() -> None:
    """A model asking for more rows than the server allows gets the ceiling."""
    _seed(5)
    server = _server()
    _register_list(server)
    payload = _call(server, {"limit": 1000}, build_mcp_config(max_page_size=2))["structuredContent"]
    assert len(payload["items"]) == 2
    # The clamp stays honest because the envelope says there is more: 5 rows
    # over a clamped limit of 2 is 3 pages, not 1.
    assert (payload["totalPages"], payload["hasNext"]) == (3, True)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("binding", "server_bound", "limit", "served"),
    [
        (UNSET, 100, 3, 3),
        (1, 100, 50, 1),
        # ``None`` is a deliberate answer, not "unset": this tool serves any limit.
        (None, 2, 50, 5),
        (UNSET, None, 50, 5),
    ],
    ids=["under-the-ceiling", "binding-overrides", "binding-none", "server-none"],
)
def test_the_effective_page_ceiling(
    binding: Any, server_bound: int | None, limit: int, served: int
) -> None:
    _seed(5)
    server = _server()
    _register_list(server, max_page_size=binding)
    out = _call(server, {"limit": limit}, build_mcp_config(max_page_size=server_bound))
    assert len(out["structuredContent"]["items"]) == served


@pytest.mark.parametrize(("binding", "maximum"), [(25, 25), (None, None)], ids=["25", "none"])
def test_tools_list_advertises_the_effective_ceiling(binding: Any, maximum: int | None) -> None:
    """The model is told the bound, so it can ask for something serveable."""
    server = _server()
    _register_list(server, max_page_size=binding)
    out = handle_tools_list(None, _ctx(server, build_mcp_config(max_page_size=500)))
    assert out["tools"][0]["inputSchema"]["properties"]["limit"].get("maximum") == maximum


# ---------- the row ceiling on an unpaginated list ----------
#
# The ceiling is the most rows one LIST tool result carries, paged or not. Only
# the enforcement differs by arm: a page clamps and says so in its envelope,
# while an unpaginated result, which carries nothing that could say rows were
# dropped, refuses, as the byte ceiling does.


@pytest.mark.django_db
def test_an_unpaginated_list_over_the_ceiling_is_refused() -> None:
    _seed(5)
    server = _server()
    _register(server)
    error = tool_error(_call(server, {}, build_mcp_config(max_page_size=2)))
    assert "2 row ceiling" in error["message"]
    # Same contract as the byte ceiling: refuse, never truncate.
    assert "not truncated" in error["message"]
    assert error["type"] == "result_too_large"


@pytest.mark.django_db
def test_a_result_exactly_at_the_ceiling_is_served_whole() -> None:
    """The boundary is inclusive: the ceiling is a servable result."""
    _seed(2)
    server = _server()
    _register(server)
    assert len(_call(server, {}, build_mcp_config(max_page_size=2))["structuredContent"]) == 2


@pytest.mark.django_db
def test_the_ceiling_bounds_the_fetch_and_not_just_the_payload(
    django_assert_num_queries: Any,
) -> None:
    """The rows are bounded in SQL, so the table is never in memory, which is
    what ``MAX_RESULT_BYTES`` cannot do: it measures a payload already fetched."""
    _seed(5)
    server = _server()
    _register(server)
    with django_assert_num_queries(1) as captured:
        _call(server, {}, build_mcp_config(max_page_size=2))
    # Ceiling + 1, the one extra row being how "over" is told from "exactly at".
    assert "LIMIT 3" in captured.captured_queries[0]["sql"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("binding", "server_bound"), [(None, 2), (UNSET, None)], ids=["binding-none", "server-none"]
)
def test_a_disabled_ceiling_serves_an_unpaginated_list_whole(
    binding: Any, server_bound: int | None
) -> None:
    _seed(5)
    server = _server()
    _register(server, max_page_size=binding)
    out = _call(server, {}, build_mcp_config(max_page_size=server_bound))
    assert len(out["structuredContent"]) == 5


@pytest.mark.django_db
@pytest.mark.parametrize(
    "selector",
    [_rows, lambda: Invoice.objects, lambda: (row for row in _rows())],
    ids=["sequence", "manager", "generator"],
)
def test_any_list_result_is_bounded_by_the_same_ceiling(selector: Any) -> None:
    """A plain sequence, a generator and a Manager are bounded as a QuerySet is."""
    _seed(3)
    server = _server()
    _register(server, SelectorSpec(kind=SelectorKind.LIST, selector=selector, permissions=OPEN))
    error = tool_error(_call(server, {}, build_mcp_config(max_page_size=2)))
    assert error["type"] == "result_too_large"
    assert "2 row ceiling" in error["message"]


def test_a_service_tool_returning_rows_is_not_row_bounded() -> None:
    """The row ceiling is a ``LIST`` tool's: a service's result is bounded by bytes."""
    server = _server()
    _register(server, ServiceSpec(service=_rows, permissions=OPEN, atomic=False))
    out = _call(server, {}, build_mcp_config(max_page_size=2))
    assert len(out["structuredContent"]) == 5


@pytest.mark.django_db(transaction=True)
async def test_the_async_path_enforces_the_same_row_ceiling() -> None:
    await sync_to_async(_seed)(5)
    server = _server()
    _register(server)
    out = await handle_tools_call_async(
        {"name": "invoices.list", "arguments": {}}, _ctx(server, build_mcp_config(max_page_size=2))
    )
    assert "2 row ceiling" in tool_error(out)["message"]


@pytest.mark.django_db
def test_call_tool_enforces_the_same_row_ceiling() -> None:
    _seed(5)
    server = MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=None,
        config=build_mcp_config(max_page_size=2),
    )
    _register(server)
    result = server.call_tool("invoices.list", user=None)
    assert "2 row ceiling" in tool_error(result.to_dict())["message"]


# ---------- the page argument's upper clamp ----------


@pytest.mark.django_db
def test_a_page_past_the_end_clamps_to_the_last_page() -> None:
    """An unclamped ``page`` becomes an arbitrarily large SQL OFFSET, which a
    backend may reject with a ``DatabaseError`` that escapes as a 500."""
    _seed(5)
    server = _server()
    _register_list(server)
    out = _call(server, {"page": 10**20, "limit": 2}, build_mcp_config(max_page_size=100))
    payload = out["structuredContent"]
    assert (payload["page"], payload["totalPages"], payload["hasNext"]) == (3, 3, False)
    assert [item["number"] for item in payload["items"]] == ["INV-4"]


@pytest.mark.django_db
def test_a_page_inside_the_range_is_untouched() -> None:
    _seed(5)
    server = _server()
    _register_list(server)
    payload = _call(server, {"page": 2, "limit": 2})["structuredContent"]
    assert (payload["page"], payload["hasNext"]) == (2, True)
    assert [item["number"] for item in payload["items"]] == ["INV-2", "INV-3"]


@pytest.mark.django_db
def test_a_page_past_the_end_of_an_empty_result_stays_at_one() -> None:
    """Nothing to clamp to: page 1 of 1, empty, rather than page 0."""
    server = _server()
    _register_list(server)
    payload = _call(server, {"page": 500})["structuredContent"]
    assert (payload["page"], payload["totalPages"], payload["items"]) == (1, 1, [])


def test_an_in_memory_paginated_result_clamps_its_page_too() -> None:
    server = _server()
    _register(
        server,
        SelectorSpec(kind=SelectorKind.LIST, selector=_rows, permissions=OPEN),
        paginate=True,
    )
    payload = _call(server, {"page": 99, "limit": 2})["structuredContent"]
    assert (payload["page"], payload["items"]) == (3, [{"number": "N-4"}])


# ---------- result-size ceiling: tools/call ----------


@pytest.mark.django_db
def test_an_oversized_tool_result_becomes_an_actionable_error() -> None:
    """Over the ceiling the call fails loudly; it is never truncated."""
    _seed(20)
    server = _server()
    _register_list(server)
    out = _call(server, {"limit": 20}, build_mcp_config(max_result_bytes=200))
    message = tool_error(out)["message"]
    assert "over this server's 200 byte ceiling" in message
    assert "not truncated" in message
    # No partial payload came back alongside the error.
    assert "items" not in str(out.get("structuredContent", ""))


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("binding", "server_bound", "seeded"),
    [(UNSET, 1_000_000, 3), (None, 200, 20)],
    ids=["under-the-ceiling", "binding-none-overrides"],
)
def test_a_result_the_ceiling_admits_is_returned_whole(
    binding: Any, server_bound: int, seeded: int
) -> None:
    _seed(seeded)
    server = _server()
    _register_list(server, max_result_bytes=binding)
    out = _call(server, {"limit": 20}, build_mcp_config(max_result_bytes=server_bound))
    assert len(out["structuredContent"]["items"]) == seeded


class _DenyAll(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:
        return False


def test_a_protocol_error_passes_through_the_ceiling_untouched() -> None:
    server = _server()
    _register(
        server,
        SelectorSpec(kind=SelectorKind.LIST, selector=list, permissions=[_DenyAll()]),
        name="invoices.denied",
        paginate=True,
    )
    out = handle_tools_call(
        {"name": "invoices.denied", "arguments": {}},
        _ctx(server, build_mcp_config(max_result_bytes=1)),
    )
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.django_db(transaction=True)
async def test_the_async_path_enforces_the_same_byte_ceiling() -> None:
    await sync_to_async(_seed)(20)
    server = _server()
    _register_list(server)
    out = await handle_tools_call_async(
        {"name": "invoices.list", "arguments": {"limit": 20}},
        _ctx(server, build_mcp_config(max_result_bytes=200)),
    )
    assert "byte ceiling" in tool_error(out)["message"]


# ---------- result-size ceiling: resources/read ----------


def _big_resource() -> dict[str, Any]:
    return {"rows": ["x" * 100 for _ in range(50)]}


def _register_big_resource(server: MCPServer) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        server.register_resource(
            SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_big_resource, permissions=OPEN),
            name="big",
            uri_template="big://all",
        )


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_an_oversized_resource_read_is_a_protocol_error(is_async: bool) -> None:
    server = _server()
    _register_big_resource(server)
    read = async_to_sync(handle_resources_read_async) if is_async else handle_resources_read
    out = read({"uri": "big://all"}, _ctx(server, build_mcp_config(max_result_bytes=200)))
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.SERVER_ERROR
    assert "byte ceiling" in out.message
    assert "Resource 'big://all'" in out.message


def test_a_resource_read_under_the_ceiling_is_returned() -> None:
    server = _server()
    _register_big_resource(server)
    out = handle_resources_read(
        {"uri": "big://all"}, _ctx(server, build_mcp_config(max_result_bytes=1_000_000))
    )
    assert out["contents"][0]["uri"] == "big://all"


# ---------- result-size ceiling: prompts/get ----------


def _big_prompt_server() -> MCPServer:
    server = _server()
    server.prompts.register(
        PromptBinding(name="big", description=None, render=lambda **_: "x" * 5_000)
    )
    return server


async def test_an_oversized_prompt_render_is_a_protocol_error() -> None:
    """``prompts/get`` was the one method the ceiling skipped."""
    config = build_mcp_config(max_result_bytes=200)
    for out in (
        await sync_to_async(handle_prompts_get)(
            {"name": "big"}, _ctx(_big_prompt_server(), config)
        ),
        await handle_prompts_get_async({"name": "big"}, _ctx(_big_prompt_server(), config)),
    ):
        assert isinstance(out, JsonRpcError)
        assert out.code == JsonRpcErrorCode.SERVER_ERROR
        assert "byte ceiling" in out.message
        assert "Prompt 'big'" in out.message


def test_a_prompt_render_under_the_ceiling_is_returned() -> None:
    out = handle_prompts_get(
        {"name": "big"}, _ctx(_big_prompt_server(), build_mcp_config(max_result_bytes=1_000_000))
    )
    assert out["messages"][0]["content"]["text"].startswith("xxx")


# ---------- dispatch deadline ----------


def _slow() -> list[Any]:
    time.sleep(0.5)
    return []


def _slow_server(**knobs: Any) -> MCPServer:
    server = _server()
    _register(
        server,
        SelectorSpec(kind=SelectorKind.LIST, selector=_slow, permissions=OPEN),
        name="invoices.slow",
        paginate=True,
        **knobs,
    )
    return server


async def test_an_overrunning_tool_call_returns_a_terminal_error() -> None:
    out = await handle_tools_call_async(
        {"name": "invoices.slow", "arguments": {}},
        _ctx(_slow_server(), build_mcp_config(dispatch_timeout=0.01)),
    )
    message = tool_error(out)["message"]
    # Honest that the work may still be running: cancelling the task does not
    # interrupt a thread parked in the DB driver.
    assert "dispatch deadline" in message
    assert "may still be running" in message


async def test_a_per_binding_deadline_overrides_the_server() -> None:
    out = await handle_tools_call_async(
        {"name": "invoices.slow", "arguments": {}},
        _ctx(_slow_server(dispatch_timeout=None), build_mcp_config(dispatch_timeout=0.01)),
    )
    # No deadline for this tool: it ran to completion despite the server's.
    assert out["structuredContent"]["items"] == []


async def test_a_resources_read_deadline_is_a_protocol_error() -> None:
    def _slow_resource() -> Any:
        time.sleep(0.5)
        return {"ok": True}

    server = _server()
    server.register_resource(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_slow_resource, permissions=OPEN),
        name="slow",
        uri_template="slow://all",
    )
    out = await adispatch(
        "resources/read",
        {"uri": "slow://all"},
        _ctx(server, build_mcp_config(dispatch_timeout=0.01)),
    )
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.SERVER_ERROR
    assert "dispatch deadline" in out.message


async def test_tools_call_is_not_double_wrapped_by_the_dispatcher() -> None:
    out = await adispatch(
        "tools/call",
        {"name": "invoices.slow", "arguments": {}},
        _ctx(_slow_server(dispatch_timeout=None), build_mcp_config(dispatch_timeout=0.01)),
    )
    # A second timer at the dispatcher would have fired here regardless of the
    # binding's opt-out.
    assert out["structuredContent"]["items"] == []


async def test_run_with_deadline_without_a_deadline_awaits_normally() -> None:
    async def _work() -> str:
        return "done"

    assert await run_with_deadline(_work(), None) == "done"


async def test_run_with_deadline_raises_past_the_deadline() -> None:
    async def _work() -> str:
        await asyncio.sleep(0.5)
        return "done"  # pragma: no cover - the deadline fires first

    with pytest.raises(asyncio.TimeoutError):
        await run_with_deadline(_work(), 0.01)


# ---------- the UNSET / None distinction ----------


def test_resolve_bound_treats_unset_as_defer_and_none_as_disabled() -> None:
    assert resolve_bound(UNSET, 500) == 500
    assert resolve_bound(None, 500) is None
    assert resolve_bound(10, 500) == 10


def test_build_mcp_config_distinguishes_omitted_from_disabled() -> None:
    assert build_mcp_config().max_page_size == 100
    assert build_mcp_config(max_page_size=None).max_page_size is None
    assert build_mcp_config(max_page_size=10).max_page_size == 10
    assert build_mcp_config(max_result_bytes=None).max_result_bytes is None
    assert build_mcp_config(max_result_bytes=7).max_result_bytes == 7
    assert build_mcp_config(dispatch_timeout=None).dispatch_timeout is None
    assert build_mcp_config(dispatch_timeout=1.5).dispatch_timeout == 1.5
    assert build_mcp_config().dispatch_timeout == 60.0
    assert build_mcp_config().max_result_bytes == 5_242_880
