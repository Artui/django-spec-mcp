"""``MCPServer.list_tools`` / ``acall_tool``, the public in-process transport, over kernel specs.

The full-transport in-process surface (distinct from :meth:`MCPServer.call_tool`):
tool listing with the merged ``inputSchema``, the per-caller listing filter and
pagination, and tool execution with the transport's scope gate and the read
pipeline's pagination. These tests prove the methods delegate to the wire
handlers (the behaviour itself is covered under ``tests/handlers/``) and build
the call context correctly off-HTTP.

The drf-services original is kept beside this file as
``test_mcp_server_in_process_drfs.py``. Three of its questions change shape on
the kernel: ordering came from a ``filter_set``'s ``OrderingFilter``, which a
kernel spec does not have, so the read extras asked here are pagination alone;
the transport-level ``MCPPermission`` gate is the ``scopes=`` gate; and a DRF
``permission_classes`` denial is the kernel spec's own ``PermissionCheck``. The
DB-backed listing permission (``DjangoPermRequired``) has no kernel binding
counterpart, because a kernel binding's only listing gate is its scopes, which
read the token and never the database.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import JsonRpcError, JsonRpcErrorCode, MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.conf import get_setting
from django_spec_mcp.handlers.kernel_utils import carry_token
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.kernel_shapes import InvoiceInput, InvoiceOutput
from tests.testapp.models import Invoice
from tests.testapp.services import invoice_by_pk, record_invoice

OPEN = [Unrestricted()]


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


class _Deny(PermissionCheck):
    def has_permission(self, *args: Any, **kwargs: Any) -> bool:
        return False


def _create(**kwargs: Any) -> ServiceSpec:
    return ServiceSpec(
        service=record_invoice,
        validator=DataclassValidator(InvoiceInput),
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=OPEN,
        **kwargs,
    )


def _invoices_cheapest_first(*, number_prefix: str = "") -> Any:
    return Invoice.objects.filter(number__startswith=number_prefix).order_by("amount_cents")


def _list() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_invoices_cheapest_first,
        reads=Parameters([Parameter("number_prefix", "string", required=False)]),
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=OPEN,
    )


# ----- list_tools -----


def test_list_tools_returns_the_merged_input_schema() -> None:
    server = _server()
    server.register_tool(_list(), name="invoices.list", description="d", paginate=True)
    payload = server.list_tools(user=None)
    assert isinstance(payload, dict)
    (tool,) = payload["tools"]
    assert tool["name"] == "invoices.list"
    # The wire's merged schema: the spec's own read plus ``page`` / ``limit``
    # from the binding's pagination knob.
    assert set(tool["inputSchema"]["properties"]) == {"number_prefix", "page", "limit"}


def test_list_tools_paginates_with_an_opaque_cursor(settings: Any) -> None:
    settings.SPEC_MCP = {"PAGE_SIZE": 1}
    server = _server()
    for name in ("a", "b"):
        server.register_tool(_create(), name=name, description="d")

    first = server.list_tools(user=None)
    assert isinstance(first, dict)
    assert [t["name"] for t in first["tools"]] == ["a"]
    cursor = first["nextCursor"]
    assert cursor is not None

    second = server.list_tools(cursor, user=None)
    assert isinstance(second, dict)
    assert [t["name"] for t in second["tools"]] == ["b"]
    assert second.get("nextCursor") is None


def test_list_tools_bad_cursor_is_a_jsonrpc_error() -> None:
    result = _server().list_tools("not-a-real-cursor", user=None)
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.INVALID_PARAMS


def test_list_tools_filters_by_the_scope_gate(settings: Any) -> None:
    settings.SPEC_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": True}
    server = _server()
    server.register_tool(_create(), name="hidden", description="d", scopes=("x",))
    payload = server.list_tools(user=None)
    assert isinstance(payload, dict)
    assert payload["tools"] == []


# ----- acall_tool -----


@pytest.mark.django_db(transaction=True)
async def test_acall_tool_runs_a_service_tool() -> None:
    server = _server()
    server.register_tool(_create(), name="invoices.create", description="d")
    out = await server.acall_tool(
        "invoices.create", {"number": "A-1", "amount_cents": 100}, user=None
    )
    assert isinstance(out, dict)
    assert out["structuredContent"]["number"] == "A-1"


@pytest.mark.django_db(transaction=True)
async def test_acall_tool_applies_the_read_pipelines_pagination() -> None:
    """Pagination, the read-shaped extra ``call_tool`` layers on too, applies
    through ``acall_tool`` beside the spec's own read."""

    @sync_to_async
    def _setup() -> None:
        for number, cents in (("A", 300), ("B", 100), ("C", 200), ("X", 50)):
            Invoice.objects.create(number=number, amount_cents=cents)

    await _setup()
    server = _server()
    server.register_tool(_list(), name="invoices.list", description="d", paginate=True)
    out = await server.acall_tool("invoices.list", {"page": 1, "limit": 2}, user=None)
    assert isinstance(out, dict)
    payload = out["structuredContent"]
    assert payload["totalPages"] == 2
    assert [item["number"] for item in payload["items"]] == ["X", "B"]


async def test_acall_tool_enforces_the_scope_gate() -> None:
    """The transport's scope gate, which the spec's permissions know nothing of,
    gates ``acall_tool``."""
    server = _server()
    server.register_tool(_create(), name="locked", description="d", scopes=("x",))
    result = await server.acall_tool("locked", {"number": "A"}, user=None)
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.django_db(transaction=True)
async def test_acall_tool_enforces_the_selector_specs_permissions() -> None:
    server = _server()
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=invoice_by_pk,
            reads=Parameters([Parameter("pk", "integer", required=True)]),
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[_Deny()],
        ),
        name="secret",
        description="d",
    )
    result = await server.acall_tool("secret", {"pk": 1}, user=None)
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.FORBIDDEN


async def test_acall_tool_unknown_tool_is_a_jsonrpc_error() -> None:
    # ``arguments`` omitted (defaults to ``None`` then ``{}``) and ``request``
    # omitted (a context is synthesised): the bare-minimum call shape.
    result = await _server().acall_tool("nope", user=None)
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.INVALID_PARAMS


# ----- _call_context -----


def test_call_context_synthesises_a_request_bearing_the_user() -> None:
    server = _server()
    user = object()
    ctx = server._call_context(user=user)
    assert ctx.http_request.user is user  # type: ignore[attr-defined]
    assert ctx.http_request.method == "POST"
    assert ctx.token.user is user
    assert ctx.tools is server.tools
    # The advertised protocol version tracks the server config, not a literal.
    assert ctx.protocol_version == get_setting("PROTOCOL_VERSIONS")[0]


def test_call_context_leaves_a_provided_request_untouched() -> None:
    server = _server()
    request = HttpRequest()
    request.method = "GET"
    sentinel = object()
    request.user = sentinel  # type: ignore[attr-defined]
    ctx = server._call_context(user=None, request=request)
    # What every handler does with the request before handing it on.
    carry_token(ctx.http_request, ctx.token)
    # The call works on a copy carrying what the real request does (its method
    # not forced to POST); the caller's own request is left as it was.
    assert ctx.http_request is not request
    assert ctx.http_request.method == "GET"
    assert request.user is sentinel  # type: ignore[attr-defined]
    assert not hasattr(request, "mcp_token")


# ----- scopes on the in-process surface -----


def test_call_context_carries_scopes() -> None:
    server = _server()
    assert server._call_context(user=None).token.scopes == ()
    assert server._call_context(user=None, scopes=["a", "b"]).token.scopes == ("a", "b")


def test_list_tools_scopes_reveal_a_scope_gated_tool(settings: Any) -> None:
    settings.SPEC_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": True}
    server = _server()
    server.register_tool(_create(), name="gated", description="d", scopes=("invoices:read",))
    # Without the scope the gated tool is invisible; supplying it reveals the tool.
    assert server.list_tools(user=None)["tools"] == []  # type: ignore[index]
    revealed = server.list_tools(user=None, scopes=["invoices:read"])
    assert isinstance(revealed, dict)
    assert [t["name"] for t in revealed["tools"]] == ["gated"]


@pytest.mark.django_db(transaction=True)
async def test_acall_tool_scopes_allow_a_scope_gated_call() -> None:
    server = _server()
    server.register_tool(_create(), name="gated", description="d", scopes=("invoices:write",))
    denied = await server.acall_tool("gated", {"number": "A"}, user=None)
    assert isinstance(denied, JsonRpcError)
    assert denied.code == JsonRpcErrorCode.FORBIDDEN
    allowed = await server.acall_tool(
        "gated", {"number": "A"}, user=None, scopes=["invoices:write"]
    )
    assert isinstance(allowed, dict)
    assert allowed.get("isError") is not True


# ----- alist_tools -----


async def test_alist_tools_mirrors_list_tools() -> None:
    server = _server()
    server.register_tool(_create(), name="a", description="d")
    assert await server.alist_tools(user=None) == server.list_tools(user=None)


async def test_alist_tools_honors_scopes(settings: Any) -> None:
    settings.SPEC_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": True}
    server = _server()
    server.register_tool(_create(), name="gated", description="d", scopes=("s",))
    payload = await server.alist_tools(user=None, scopes=["s"])
    assert isinstance(payload, dict)
    assert [t["name"] for t in payload["tools"]] == ["gated"]
