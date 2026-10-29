"""``page`` and ``limit`` reach a kernel spec alike on every route.

``call_tool``, ``acall_tool`` and both wire handlers hand a selector the same
arguments, ``page`` and ``limit`` taken by the pagination on a ``LIST`` and left
alone elsewhere. The pagination is ``paginate=True`` on ``register_tool``, and
the split is ``page_request``'s on every route, so the questions are: a
paginated ``LIST`` never hands either name to its selector; an unpaginated one,
a ``RETRIEVE`` and a service tool take them as their own reads, a filter set's
filter named ``limit`` included. A kernel spec declares its reads, and nothing
sits between the wire and them.

A paginated ``LIST`` whose spec declares a read named ``page`` or ``limit`` is
refused at registration, because the pagination would take the value the read
advertises and the selector would never receive it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import django_filters
import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django_service_specs import (
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from tests.testapp.models import Invoice

pytestmark = pytest.mark.django_db(transaction=True)

_ROUTES = pytest.mark.parametrize("route", ["call_tool", "acall_tool", "handler", "async_handler"])
OPEN = [Unrestricted()]
_PAGE_AND_LIMIT = [Parameter("page", "integer"), Parameter("limit", "integer")]
_STATUS = Parameter("status", "string")


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        conventions=server.conventions,
    )


async def _structured(server: MCPServer, route: str, arguments: dict[str, Any]) -> Any:
    """What calling ``read`` through ``route`` returns as structured content."""
    if route == "call_tool":
        result: Any = await sync_to_async(server.call_tool)("read", arguments, user=None)
        out: Any = result.to_dict()
    elif route == "acall_tool":
        out = await server.acall_tool("read", arguments, user=None)
    else:
        params: dict[str, Any] = {"name": "read", "arguments": arguments}
        handler: Any = (
            handle_tools_call_async
            if route == "async_handler"
            else sync_to_async(handle_tools_call)
        )
        out = await handler(params, _ctx(server))
    assert isinstance(out, dict), f"answered {out!r}"
    assert out.get("isError") is not True, out
    return out["structuredContent"]


def _server(spec: Any, **tool: Any) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(spec, name="read", description="Read.", **tool)
    return server


def _sent(**kwargs: Any) -> dict[str, Any]:
    """What the caller sent, out of the pool the selector was handed."""
    return {name: kwargs[name] for name in ("page", "limit", "status") if name in kwargs}


def _selector(kind: SelectorKind, *reads: Parameter) -> SelectorSpec:
    def read(**kwargs: Any) -> Any:
        sent = _sent(**kwargs)
        return [sent] if kind is SelectorKind.LIST else sent

    return SelectorSpec(kind=kind, selector=read, reads=Parameters(list(reads)), permissions=OPEN)


@_ROUTES
async def test_a_paginated_list_never_hands_its_selector_page_or_limit(route: str) -> None:
    server = _server(_selector(SelectorKind.LIST, _STATUS), paginate=True)

    out = await _structured(server, route, {"page": 1, "limit": 5, "status": "open"})

    assert out["items"] == [{"status": "open"}]
    assert (out["page"], out["hasNext"]) == (1, False)


@_ROUTES
@pytest.mark.parametrize("kind", [SelectorKind.LIST, SelectorKind.RETRIEVE])
async def test_a_selector_nothing_paginates_takes_page_and_limit_as_its_reads(
    route: str, kind: SelectorKind
) -> None:
    sent = {"page": 2, "limit": 5, "status": "open"}

    out = await _structured(_server(_selector(kind, *_PAGE_AND_LIMIT, _STATUS)), route, sent)

    assert out == ([sent] if kind is SelectorKind.LIST else sent)


class _AmountCeiling(django_filters.FilterSet):
    """A ``FilterSet`` declaring ``limit`` as a filter of its own."""

    limit = django_filters.NumberFilter(field_name="amount_cents", lookup_expr="lte")

    class Meta:
        model = Invoice
        fields: list[str] = []


@_ROUTES
async def test_a_filter_set_reads_a_pagination_named_filter_on_every_route(route: str) -> None:
    await Invoice.objects.acreate(number="A-1", amount_cents=50)
    await Invoice.objects.acreate(number="A-2", amount_cents=500)
    server = _server(
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda: Invoice.objects.order_by("pk").values("number"),
            filter_set=_AmountCeiling,
            permissions=OPEN,
        )
    )

    out = await _structured(server, route, {"limit": 100})

    assert out == [{"number": "A-1"}]


@dataclass
class _PageAndLimit:
    page: int
    limit: int


@_ROUTES
async def test_a_service_tools_page_and_limit_are_its_own_on_every_route(route: str) -> None:
    # No read pipeline paginates a service tool, so nothing takes either name.
    server = _server(
        ServiceSpec(
            service=lambda *, page, limit: {"page": page, "limit": limit},
            validator=DataclassValidator(_PageAndLimit),
            atomic=False,
            permissions=OPEN,
        )
    )

    out = await _structured(server, route, {"page": 2, "limit": 5})

    assert out == {"page": 2, "limit": 5}


@pytest.mark.parametrize("name", ["page", "limit"])
def test_a_paginated_list_declaring_a_read_named_page_or_limit_is_refused(name: str) -> None:
    # The name as the registration message lists it, so a message that only
    # mentions ``page`` and ``limit`` in passing cannot satisfy the match.
    with pytest.raises(ImproperlyConfigured, match=re.escape(f"['{name}']")):
        _server(_selector(SelectorKind.LIST, Parameter(name, "integer")), paginate=True)
