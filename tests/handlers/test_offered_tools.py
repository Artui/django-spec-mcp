from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from django.db.models import Q
from django.http import HttpRequest
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    Affordance,
    PoolSeeds,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
    seeded_pool,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.handle_tools_list import handle_tools_list
from django_spec_mcp.handlers.types.context import MCPCallContext
from tests.utils import call_context, make_server, tool_error

_POOL = "django_spec_mcp.handlers.offered_tools.seeded_pool"
"""Where the filter looks ``seeded_pool`` up, so a spy there sees every pool it builds."""

_CLOSED = Affordance(code="books_closed", reason="The books are closed.", when=lambda: False)
_OPEN = Affordance(code="books_open", reason="The books are open.", when=lambda: True)

_REFUSED: dict[str, Any] = {
    "type": "service_error",
    "message": "The books are closed.",
    "code": "books_closed",
}


class _User:
    """A caller with a primary key, as the transport requires of an authenticated one."""

    pk = 7
    is_authenticated = True


class _UntouchableRowCondition:
    """A condition on the row that records any attempt to answer it."""

    conditional = True

    def __init__(self) -> None:
        self.touched: list[str] = []

    def resolve_expression(self, *args: Any, **kwargs: Any) -> Any:
        self.touched.append("resolve_expression")
        raise AssertionError("a row condition was compiled at list time")

    def __call__(self, *args: Any, **kwargs: Any) -> bool:
        self.touched.append("__call__")
        return False


def _service(*affordances: Affordance) -> ServiceSpec:
    return ServiceSpec(
        service=lambda: {"status": "ran"},
        atomic=False,
        affordances=list(affordances) or None,
        permissions=[Unrestricted()],
    )


def _server(**config: Any) -> MCPServer:
    return make_server(
        session_store=None,
        config=build_mcp_config(**config) if config else None,
    )


def _ctx(server: MCPServer, *, user: Any = None, http_request: Any = None) -> MCPCallContext:
    overrides: dict[str, Any] = {"token": TokenInfo(user=user)}
    if http_request is not None:
        overrides["http_request"] = http_request
    return call_context(server, **overrides)


def _listed(server: MCPServer, **kwargs: Any) -> list[str]:
    listing: Any = handle_tools_list(None, _ctx(server, **kwargs))
    return [tool["name"] for tool in listing["tools"]]


def _pool_spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record every pool the filter builds, still building it for real."""
    built: list[dict[str, Any]] = []

    def spy(**kwargs: Any) -> dict[str, Any]:
        pool = seeded_pool(**kwargs)
        built.append(pool)
        return pool

    monkeypatch.setattr(_POOL, spy)
    return built


# ---------- the answer decides the listing ----------


@pytest.mark.parametrize("filter_by_permissions", [False, True], ids=["filter-off", "filter-on"])
def test_an_unmet_operation_condition_leaves_the_tool_out(filter_by_permissions: bool) -> None:
    """Not gated by ``FILTER_LISTINGS_BY_PERMISSIONS``."""
    server = _server(filter_listings_by_permissions=filter_by_permissions)
    server.register_tool(_service(_CLOSED), name="close_books")
    server.register_tool(_service(), name="post_entry")

    assert _listed(server) == ["post_entry"]


def test_a_met_operation_condition_lists_the_tool() -> None:
    server = _server()
    server.register_tool(_service(_OPEN), name="close_books")

    assert _listed(server) == ["close_books"]


def test_every_operation_condition_must_be_met() -> None:
    """The first unmet one decides, wherever it is declared."""
    server = _server()
    server.register_tool(_service(_OPEN, _CLOSED), name="close_books")

    assert _listed(server) == []


@pytest.mark.django_db
def test_row_conditions_are_not_asked_at_list_time(django_assert_num_queries: Any) -> None:
    """A condition on the row has no row at list time."""
    untouchable = _UntouchableRowCondition()
    server = _server()
    server.register_tool(
        _service(
            Affordance(code="never", reason="Matches no row.", when=Q(pk__in=[])),
            Affordance(code="untouchable", reason="Never answered here.", when=untouchable),
        ),
        name="void_invoice",
    )

    with django_assert_num_queries(0):
        listed = _listed(server)

    assert listed == ["void_invoice"]
    assert untouchable.touched == []


# ---------- always_listed ----------


@pytest.mark.parametrize(
    ("filter_by_permissions", "scopes"),
    [(False, None), (True, None), (True, ("books:write",))],
    ids=["filter-off", "filter-on", "both-filters-would-drop"],
)
def test_always_listed_keeps_an_unavailable_tool(
    filter_by_permissions: bool, scopes: tuple[str, ...] | None
) -> None:
    server = _server(filter_listings_by_permissions=filter_by_permissions)
    server.register_tool(_service(_CLOSED), name="close_books", scopes=scopes, always_listed=True)

    assert _listed(server) == ["close_books"]


def test_a_tool_the_permission_filter_drops_is_never_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Availability runs after the permission filter."""
    built = _pool_spy(monkeypatch)
    server = _server(filter_listings_by_permissions=True)
    server.register_tool(_service(_CLOSED), name="close_books", scopes=("books:write",))

    assert _listed(server) == []
    assert built == []


def test_the_permission_filter_still_drops_an_available_tool() -> None:
    server = _server(filter_listings_by_permissions=True)
    server.register_tool(_service(_OPEN), name="close_books", scopes=("books:write",))
    server.register_tool(_service(_OPEN), name="post_entry")

    assert _listed(server) == ["post_entry"]


# ---------- the pool ----------


def test_a_listing_of_tools_declaring_nothing_builds_no_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Declaring nothing costs nothing: such tools are listed without a pool."""
    built = _pool_spy(monkeypatch)
    server = _server()
    server.register_tool(_service(), name="post_entry")
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda: None,
            affordances={"cancel": _service(_CLOSED)},
            permissions=[Unrestricted()],
        ),
        name="get_order",
    )
    assert _listed(server) == ["post_entry", "get_order"]
    assert built == []


def test_a_pool_is_built_per_conditioned_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """One per kernel tool asking a condition, and none for the tool declaring nothing."""
    built = _pool_spy(monkeypatch)
    server = _server()
    server.register_tool(_service(), name="post_entry")
    server.register_tool(_service(_CLOSED), name="close_books")
    server.register_tool(_service(_OPEN), name="open_books")

    assert _listed(server) == ["post_entry", "open_books"]
    assert len(built) == 2
    assert all("call_context" not in pool for pool in built)


def test_the_condition_sees_the_context_and_user_a_call_would() -> None:
    """Asked with the call's ``call_context``: the transport's own request."""
    seen: list[tuple[Any, ...]] = []

    def condition(user: Any, call_context: Any) -> bool:
        request = call_context["request"]
        seen.append((request, request.mcp_token, call_context["action"], user))
        return True

    user = _User()
    http_request = HttpRequest()
    http_request.method = "POST"
    server = _server()
    server.register_tool(
        _service(Affordance(code="books_open", reason="Closed.", when=condition)),
        name="close_books",
    )
    ctx = _ctx(server, user=user, http_request=http_request)

    listing: Any = handle_tools_list(None, ctx)
    handle_tools_call({"name": "close_books", "arguments": {}}, ctx)

    assert [tool["name"] for tool in listing["tools"]] == ["close_books"]
    at_list, at_call = seen
    assert at_list == at_call
    assert at_list == (http_request, ctx.token, "close_books", user)


def _seeded_ctx(seeds: PoolSeeds, **kwargs: Any) -> tuple[MCPServer, MCPCallContext]:
    """A server registering ``seeds``, and a context carrying them as the transport's does."""
    server = make_server(session_store=None, pool_seeds=seeds)
    return server, replace(_ctx(server, **kwargs), pool_seeds=seeds)


def _tenant_request(tenant: str) -> HttpRequest:
    http_request = HttpRequest()
    http_request.method = "POST"
    http_request.tenant = tenant
    return http_request


def _acme_only() -> Affordance:
    return Affordance(code="not_acme", reason="Acme only.", when=lambda tenant: tenant == "acme")


@pytest.mark.django_db
def test_a_seed_reading_the_request_is_resolved_for_the_listing_as_for_the_call() -> None:
    """A seed whose resolver declares ``request`` reads it off the call's ``call_context``."""
    server, ctx = _seeded_ctx(
        DEFAULT_POOL_SEEDS.extend(tenant=lambda *, request: request.tenant),
        http_request=_tenant_request("acme"),
    )
    server.register_tool(_service(), name="post_entry")
    server.register_tool(_service(_acme_only()), name="close_books")

    listing: Any = handle_tools_list(None, ctx)
    called: Any = handle_tools_call({"name": "close_books", "arguments": {}}, ctx)

    assert [tool["name"] for tool in listing["tools"]] == ["post_entry", "close_books"]
    assert called["structuredContent"] == {"status": "ran"}


@pytest.mark.django_db
def test_a_seed_defaulting_the_request_does_not_hide_a_tool_the_call_admits() -> None:
    """A resolver defaulting request to None was handed the default at list time."""
    server, ctx = _seeded_ctx(
        DEFAULT_POOL_SEEDS.extend(tenant=lambda *, request=None: getattr(request, "tenant", None)),
        http_request=_tenant_request("acme"),
    )
    server.register_tool(_service(_acme_only()), name="close_books")

    listing: Any = handle_tools_list(None, ctx)
    called: Any = handle_tools_call({"name": "close_books", "arguments": {}}, ctx)

    assert [tool["name"] for tool in listing["tools"]] == ["close_books"]
    assert called["structuredContent"] == {"status": "ran"}


@pytest.mark.django_db
def test_each_tool_s_seeds_are_resolved_with_its_own_action() -> None:
    """A resolver may declare ``call_context`` and read its ``action``, the tool's name."""
    actions: list[str] = []

    def acting_for(*, call_context: Any) -> str:
        actions.append(call_context["action"])
        return call_context["action"]

    def own_tool(acting_for: str) -> bool:
        return acting_for in {"close_books", "open_books"}

    server, ctx = _seeded_ctx(DEFAULT_POOL_SEEDS.extend(acting_for=acting_for))
    for name in ("close_books", "open_books"):
        server.register_tool(
            _service(Affordance(code="other", reason="Not this tool.", when=own_tool)), name=name
        )

    listing: Any = handle_tools_list(None, ctx)
    at_list = list(actions)
    actions.clear()
    for name in ("close_books", "open_books"):
        handle_tools_call({"name": name, "arguments": {}}, ctx)

    assert [tool["name"] for tool in listing["tools"]] == ["close_books", "open_books"]
    assert at_list == actions == ["close_books", "open_books"]


# ---------- the call is unchanged ----------


def test_a_left_out_tool_is_still_called_and_refused_with_its_code() -> None:
    """The listing is advisory: tools/call never consults it."""
    server = _server()
    server.register_tool(_service(_CLOSED), name="close_books")
    ctx = _ctx(server)

    assert _listed(server) == []
    assert tool_error(handle_tools_call({"name": "close_books", "arguments": {}}, ctx)) == _REFUSED


@pytest.mark.django_db(transaction=True)
async def test_the_async_call_refuses_a_left_out_tool_with_its_code() -> None:
    server = _server()
    server.register_tool(_service(_CLOSED), name="close_books")
    listing: Any = await server.alist_tools(user=None)

    out = await handle_tools_call_async({"name": "close_books", "arguments": {}}, _ctx(server))

    assert listing["tools"] == []
    assert tool_error(out) == _REFUSED


# ---------- pagination and caching ----------


def test_the_listing_is_filtered_before_it_is_paginated() -> None:
    """Otherwise the first page comes back short, with a cursor past an unseen tool."""
    server = _server(page_size=1)
    server.register_tool(_service(_CLOSED), name="close_books")
    server.register_tool(_service(), name="post_entry")

    listing: Any = handle_tools_list(None, _ctx(server))

    assert [tool["name"] for tool in listing["tools"]] == ["post_entry"]
    assert "nextCursor" not in listing


def test_a_listing_that_asked_a_condition_is_private() -> None:
    """The answer was asked against this caller's user and request."""
    server = _server()
    server.register_tool(_service(_OPEN), name="open_books")

    listing: Any = handle_tools_list(None, _ctx(server))

    assert listing["cacheScope"] == "private"


def test_a_listing_that_asked_nothing_stays_public() -> None:
    """Including one whose only condition-bearing tool is ``always_listed``."""
    server = _server()
    server.register_tool(_service(), name="post_entry")
    server.register_tool(_service(_CLOSED), name="close_books", always_listed=True)

    listing: Any = handle_tools_list(None, _ctx(server))

    assert listing["cacheScope"] == "public"


def test_a_tool_declaring_only_row_conditions_asks_nothing_and_stays_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool whose every affordance is a row condition is listed as one declaring nothing is."""
    built = _pool_spy(monkeypatch)
    server = _server()
    server.register_tool(
        _service(Affordance(code="never", reason="Matches no row.", when=Q(pk__in=[]))),
        name="void_invoice",
    )

    listing: Any = handle_tools_list(None, _ctx(server))

    assert [tool["name"] for tool in listing["tools"]] == ["void_invoice"]
    assert built == []
    assert listing["cacheScope"] == "public"


def test_nothing_is_remembered_between_listings() -> None:
    """The catalog cache is a hint to the client, never a copy on the server."""
    books: dict[str, bool] = {"open": False}
    server = _server()
    server.register_tool(
        _service(Affordance(code="books_closed", reason="Closed.", when=lambda: books["open"])),
        name="close_books",
    )

    before = _listed(server)
    books["open"] = True
    after = _listed(server)

    assert (before, after) == ([], ["close_books"])
