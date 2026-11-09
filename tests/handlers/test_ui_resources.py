"""An interactive view over the real ``resources/*`` handlers, both transports."""

from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest
from django_service_specs import PermissionCheck, PoolSeeds

from django_spec_mcp.handlers.handle_resources_list import handle_resources_list
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.registry.types.ui_csp import UICsp
from django_spec_mcp.registry.types.ui_resource_meta import UIResourceMeta
from django_spec_mcp.server.mcp_server import MCPServer
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.resource_encoding import ResourceEncoding
from django_spec_mcp.types.ui_meta_key import UI_META_KEY
from django_spec_mcp.types.ui_resource_mime_type import UI_RESOURCE_MIME_TYPE
from tests.utils import RefusingRateLimit, call_context, make_server

VIEW = "<!doctype html><h1>Invoices</h1>"
URI = "ui://invoices/table.html"


def _server(**overrides: Any) -> MCPServer:
    server = make_server(
        name="test",
        description="d",
        session_store=InMemorySessionStore(),
    )
    server.register_ui_resource(
        **{
            "name": "invoices_table",
            "uri": URI,
            "html": VIEW,
            "description": "Invoices, as a table.",
            **overrides,
        }
    )
    return server


def _contents(response: Any) -> dict[str, Any]:
    return response["contents"][0]


class TestRead:
    def test_returns_the_document_not_a_json_string(self) -> None:
        out = handle_resources_read({"uri": URI}, call_context(_server()))
        assert _contents(out)["text"] == VIEW

    async def test_the_async_transport_returns_the_same_body(self) -> None:
        """The two read handlers are parallel implementations, so the shared
        builder is the only thing keeping their wire shapes identical."""
        sync = handle_resources_read({"uri": URI}, call_context(_server()))
        async_ = await handle_resources_read_async({"uri": URI}, call_context(_server()))

        assert _contents(async_) == _contents(sync)

    def test_advertises_the_apps_mime_type(self) -> None:
        out = handle_resources_read({"uri": URI}, call_context(_server()))
        assert _contents(out)["mimeType"] == UI_RESOURCE_MIME_TYPE

    def test_carries_the_ui_metadata_on_the_contents_block(self) -> None:
        server = _server(ui=UIResourceMeta(csp=UICsp(connect_domains=("https://api.x",))))
        out = handle_resources_read({"uri": URI}, call_context(server))

        assert _contents(out)["_meta"] == {
            UI_META_KEY: {"csp": {"connectDomains": ["https://api.x"]}}
        }

    @pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
    async def test_a_view_whose_source_returns_a_non_string_is_a_clean_error(
        self, is_async: bool
    ) -> None:
        """Not a transport-level 500 — the client gets a JSON-RPC error."""
        server = _server(html=None, render=lambda: {"oops": True})
        context = call_context(server)
        if is_async:
            out = await handle_resources_read_async({"uri": URI}, context)
        else:
            out = handle_resources_read({"uri": URI}, context)

        assert out.code == JsonRpcErrorCode.INTERNAL_ERROR


class TestListing:
    def test_appears_in_resources_list_beside_data_resources(self) -> None:
        out = handle_resources_list(None, call_context(_server()))
        assert [r["uri"] for r in out["resources"]] == [URI]

    def test_the_listing_entry_carries_the_ui_metadata(self) -> None:
        """A host may prefetch the view straight off the listing, before any
        tool call — so the CSP has to be there too, not only on the read."""
        server = _server(ui=UIResourceMeta(prefers_border=True))
        out = handle_resources_list(None, call_context(server))

        assert out["resources"][0]["_meta"] == {UI_META_KEY: {"prefersBorder": True}}

    def test_a_concrete_ui_uri_is_not_a_template(self) -> None:
        """Brace-free, so it lands in ``resources/list`` rather than
        ``resources/templates/list``. Views are concrete by design."""
        server = _server()
        assert server.resources.templates() == []

    def test_guarding_a_view_hides_it_from_the_listing(self, settings: Any) -> None:
        settings.SPEC_MCP = {
            **settings.SPEC_MCP,
            "FILTER_LISTINGS_BY_PERMISSIONS": True,
        }

        class _Deny(PermissionCheck):
            def has_permission(self, principal: Any, spec: Any) -> bool:
                return False

        server = _server(permissions=[_Deny()])
        out = handle_resources_list(None, call_context(server))

        assert out["resources"] == []


@pytest.mark.parametrize("reader", ["sync", "async"])
async def test_a_template_backed_view_renders(reader: str) -> None:
    server = _server(html=None, template_name="mcp_ui/view.html")
    ctx = call_context(server)
    out = (
        handle_resources_read({"uri": URI}, ctx)
        if reader == "sync"
        else await handle_resources_read_async({"uri": URI}, ctx)
    )

    assert "<h1>A view</h1>" in _contents(out)["text"]


@pytest.mark.parametrize("reader", ["sync", "async"])
async def test_a_body_template_backed_view_serves_a_whole_document(reader: str) -> None:
    """The package-composed shell has to survive the read path intact — TEXT
    encoding and all — or the host receives a quoted string rather than a
    document, and renders nothing."""
    server = _server(html=None, body_template_name="mcp_ui/body.html")
    ctx = call_context(server)
    out = (
        handle_resources_read({"uri": URI}, ctx)
        if reader == "sync"
        else await handle_resources_read_async({"uri": URI}, ctx)
    )
    text = _contents(out)["text"]

    assert text.startswith("<!doctype html>")
    assert '<div id="rows">Waiting for results.</div>' in text
    assert "ui/notifications/initialized" in text


# ---------- the path a view reads on ----------


async def _read(reader: str, ctx: MCPCallContext) -> Any:
    if reader == "sync":
        return handle_resources_read({"uri": URI}, ctx)
    return await handle_resources_read_async({"uri": URI}, ctx)


@pytest.mark.parametrize("reader", ["sync", "async"])
async def test_a_pool_seed_resolved_on_a_view_read_is_handed_the_transports_request(
    reader: str,
) -> None:
    # The server's seeds are resolved into every read's pool, a view's too, and
    # one declaring ``request`` is handed the transport's ``HttpRequest`` with
    # the token on it, as on a kernel tool call.
    seen: list[Any] = []

    def tenant(request: Any) -> str:
        seen.append(request)
        return "acme"

    server = _server()
    http_request = HttpRequest()
    ctx = call_context(
        server, http_request=http_request, pool_seeds=PoolSeeds().extend(tenant=tenant)
    )

    out = await _read(reader, ctx)

    assert _contents(out)["text"] == VIEW
    assert seen == [http_request]
    assert seen[0].mcp_token is ctx.token


def _callable_view_server(selector: Any, uri: str = URI) -> MCPServer:
    # A view over a plain callable that declares what it wants of the pool.
    # ``register_ui_resource`` refuses a ``render`` taking any parameter, so such
    # a binding is built by hand, the way a binding of the kernel's own kinds is
    # not.
    server = make_server(name="test", description="d", session_store=InMemorySessionStore())
    server.resources.register(
        ResourceBinding(
            name="callable_view",
            uri_template=uri,
            description=None,
            selector=selector,
            mime_type="text/html",
            encoding=ResourceEncoding.TEXT,
        )
    )
    return server


async def _read_uri(reader: str, ctx: MCPCallContext, uri: str) -> Any:
    if reader == "sync":
        return handle_resources_read({"uri": uri}, ctx)
    return await handle_resources_read_async({"uri": uri}, ctx)


@pytest.mark.parametrize("reader", ["sync", "async"])
async def test_a_registered_request_seed_is_the_value_a_view_receives(reader: str) -> None:
    # A seed registered under ``request`` is the value a view declaring it is
    # handed, as on a kernel tool or resource, and the read does not raise
    # ``TypeError`` for the transport's own request beside it.
    sentinel = object()
    seen: list[Any] = []

    def view(request: Any) -> str:
        seen.append(request)
        return VIEW

    server = _callable_view_server(view)
    ctx = call_context(server, pool_seeds=PoolSeeds().extend(request=lambda: sentinel))

    out = await _read(reader, ctx)

    assert _contents(out)["text"] == VIEW
    assert seen == [sentinel]


@pytest.mark.parametrize("reader", ["sync", "async"])
async def test_a_registered_view_seed_is_the_value_a_view_receives(reader: str) -> None:
    # A guard, not a falsification: the read never spread a ``view`` of its own,
    # so a seed registered under that name never collided, on main either. It
    # holds that the seed stays the only value the name can take.
    sentinel = object()
    seen: list[Any] = []

    def view(view: Any) -> str:
        seen.append(view)
        return VIEW

    server = _callable_view_server(view)
    ctx = call_context(server, pool_seeds=PoolSeeds().extend(view=lambda: sentinel))

    out = await _read(reader, ctx)

    assert _contents(out)["text"] == VIEW
    assert seen == [sentinel]


@pytest.mark.parametrize("reader", ["sync", "async"])
async def test_a_view_with_no_request_seed_receives_the_transports_request(reader: str) -> None:
    # With nothing registered under ``request``, a view declaring it is handed
    # the carried ``HttpRequest``, the token on it.
    seen: list[Any] = []

    def view(request: Any) -> str:
        seen.append(request)
        return VIEW

    server = _callable_view_server(view)
    http_request = HttpRequest()
    ctx = call_context(server, http_request=http_request)

    out = await _read(reader, ctx)

    assert _contents(out)["text"] == VIEW
    assert seen == [http_request]
    assert seen[0].mcp_token is ctx.token


@pytest.mark.parametrize("reader", ["sync", "async"])
async def test_a_view_registered_publicly_reads_beside_a_request_seed(reader: str) -> None:
    # The route a project takes, ``register_ui_resource``, on a server whose seeds
    # hold ``request``: the read used to raise ``TypeError`` (base_pool() got a
    # registered pool seed 'request' that an entry already occupies).
    ctx = call_context(_server(), pool_seeds=PoolSeeds().extend(request=lambda: object()))

    out = await _read(reader, ctx)

    assert _contents(out)["text"] == VIEW


@pytest.mark.parametrize("reader", ["sync", "async"])
async def test_a_seed_resolver_declaring_view_is_resolved(reader: str) -> None:
    # A seed whose resolver declares ``view`` is asked under the call's context,
    # which has none for a view read: it receives ``None``. Before, the resolver
    # was never given the name, and every view read on such a server raised
    # ``TypeError`` (missing 1 required positional argument: 'view').
    seen: list[Any] = []

    def tenant(view: Any) -> str:
        seen.append(view)
        return "acme"

    ctx = call_context(_server(), pool_seeds=PoolSeeds().extend(tenant=tenant))

    out = await _read(reader, ctx)

    assert _contents(out)["text"] == VIEW
    assert seen == [None]


@pytest.mark.parametrize("reader", ["sync", "async"])
async def test_a_seed_resolver_declaring_call_context_reads_the_resource(reader: str) -> None:
    # The resolver reads the read's context under the resource's name, as a
    # kernel resource read's does: ``action`` is the view's registered name and
    # ``request`` the request the read arrived on, the token on it. Before, the
    # read raised ``TypeError`` (missing 1 required positional argument:
    # 'call_context').
    seen: list[Any] = []

    def tenant(call_context: Any) -> str:
        seen.append(call_context)
        return "acme"

    http_request = HttpRequest()
    ctx = call_context(
        _server(), http_request=http_request, pool_seeds=PoolSeeds().extend(tenant=tenant)
    )

    out = await _read(reader, ctx)

    assert _contents(out)["text"] == VIEW
    assert len(seen) == 1
    assert seen[0]["action"] == "invoices_table"
    assert seen[0]["request"] is http_request
    assert seen[0]["request"].mcp_token is ctx.token


@pytest.mark.parametrize("reader", ["sync", "async"])
@pytest.mark.parametrize("seeded", [False, True], ids=["unseeded", "seeded"])
async def test_a_uri_variable_named_request_never_shadows_the_transports_entry(
    reader: str, seeded: bool
) -> None:
    # ``register_ui_resource`` runs no reserved-variable guard, so a templated
    # URI of a hand-built binding may name ``request``. Its variable lands
    # first and loses to the transport's request, or to the seed registered
    # under that name.
    sentinel = object()
    seen: list[Any] = []

    def view(request: Any) -> str:
        seen.append(request)
        return VIEW

    server = _callable_view_server(view, uri="docs://{request}")
    http_request = HttpRequest()
    seeds = PoolSeeds().extend(request=lambda: sentinel) if seeded else PoolSeeds()
    ctx = call_context(server, http_request=http_request, pool_seeds=seeds)

    out = await _read_uri(reader, ctx, "docs://forged")

    assert _contents(out)["text"] == VIEW
    assert seen == [sentinel if seeded else http_request]


_SOURCES: dict[str, dict[str, Any]] = {
    "html": {},
    "template_name": {"html": None, "template_name": "mcp_ui/view.html"},
    "body_template_name": {"html": None, "body_template_name": "mcp_ui/body.html"},
    "render": {"html": None, "render": lambda: VIEW},
}


@pytest.mark.parametrize("reader", ["sync", "async"])
@pytest.mark.parametrize("source", sorted(_SOURCES))
async def test_every_view_source_serves_its_document_whole(reader: str, source: str) -> None:
    # The whole answer, for each content source: the document the source
    # produces, verbatim, under the Apps MIME type, the metadata on the block
    # and the cache hints beside it.
    server = _server(
        **_SOURCES[source],
        cache_ttl_ms=5000,
        ui=UIResourceMeta(prefers_border=True),
        meta={"k": 1},
    )
    resolved = server.resources.resolve(URI)
    assert resolved is not None
    document = resolved[0].selector()

    out = await _read(reader, call_context(server))

    assert out == {
        "contents": [
            {
                "uri": URI,
                "mimeType": UI_RESOURCE_MIME_TYPE,
                "text": document,
                "_meta": {UI_META_KEY: {"prefersBorder": True}, "k": 1},
            }
        ],
        "ttlMs": 5000,
        "cacheScope": "private",
    }


@pytest.mark.parametrize("reader", ["sync", "async"])
async def test_a_view_its_scope_gate_refuses_is_refused_with_the_scopes(reader: str) -> None:
    out = await _read(reader, call_context(_server(scopes="views:read")))

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN
    assert out.data == {"requiredScopes": ["views:read"]}


@pytest.mark.parametrize("reader", ["sync", "async"])
async def test_a_view_over_its_rate_limit_is_refused_with_the_retry(reader: str) -> None:
    limit = RefusingRateLimit()

    out = await _read(reader, call_context(_server(rate_limits=[limit])))

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.RATE_LIMITED
    assert out.data == {"retryAfter": 30}
    assert limit.consumed == 1
