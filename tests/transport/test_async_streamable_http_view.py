"""The async streamable HTTP view, serving the test app's kernel operations.

Every call here reaches a kernel spec registered with ``register_tool`` or
``register_resource``: ``build_kernel_server`` mounted on the async URL conf.

The view is a plain Django view, which no framework setting reaches, and what a
tool raising is answered with is held by ``test_dispatch_backstop.py``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from asgiref.sync import iscoroutinefunction
from django import VERSION as DJANGO_VERSION
from django.test import AsyncClient, override_settings
from django_service_specs import PermissionCheck, ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.registry.resource_registry import ResourceRegistry
from django_spec_mcp.registry.tool_registry import ToolRegistry
from django_spec_mcp.transport.async_streamable_http_view import (
    AsyncStreamableHttpView,
)
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.mcp import build_kernel_server
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import nested_past_the_decoders_limit_here, on_a_bounded_stack


@pytest.fixture
def async_urlconf():
    """Mount the kernel server on the async URL conf for one test."""
    with override_settings(ROOT_URLCONF=urlconf_for(build_kernel_server(), is_async=True)):
        yield


async def _initialize(client: AsyncClient) -> str:
    response = await client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0.0"},
                },
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 200
    return response["Mcp-Session-Id"]


async def test_async_tools_list(async_urlconf) -> None:
    client = AsyncClient()
    sid = await _initialize(client)
    response = await client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
    )
    body = response.json()
    names = [t["name"] for t in body["result"]["tools"]]
    assert "invoices.create" in names


@pytest.mark.django_db(transaction=True)
async def test_async_tools_call_creates_invoice(async_urlconf) -> None:
    """The async dispatch path runs the kernel's ``adispatch`` end-to-end against a real DB."""
    client = AsyncClient()
    sid = await _initialize(client)
    response = await client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "invoices.create",
                    "arguments": {"number": "ASYNC-1", "amount_cents": 250},
                },
            }
        ),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
    )
    body = response.json()
    assert "result" in body, body
    assert body["result"]["structuredContent"]["number"] == "ASYNC-1"


@pytest.mark.django_db(transaction=True)
async def test_async_resources_read(async_urlconf) -> None:
    client = AsyncClient()
    sid = await _initialize(client)
    create = await client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "invoices.create",
                    "arguments": {"number": "ASYNC-2", "amount_cents": 500},
                },
            }
        ),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
    )
    invoice_id = create.json()["result"]["structuredContent"]["id"]

    read = await client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "resources/read",
                "params": {"uri": f"invoices://{invoice_id}"},
            }
        ),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
    )
    text = read.json()["result"]["contents"][0]["text"]
    assert json.loads(text)["number"] == "ASYNC-2"


async def test_async_get_unknown_session_returns_404(async_urlconf) -> None:
    client = AsyncClient()
    response = await client.get(
        "/mcp/",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": "not-a-session"},
    )
    assert response.status_code == 404


async def test_async_get_missing_protocol_version_returns_400(async_urlconf) -> None:
    client = AsyncClient()
    response = await client.get("/mcp/")
    assert response.status_code == 400


async def test_async_delete_terminates_session(async_urlconf) -> None:
    client = AsyncClient()
    sid = await _initialize(client)
    response = await client.delete("/mcp/", headers={"Mcp-Session-Id": sid})
    assert response.status_code == 204
    follow_up = await client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
    )
    assert follow_up.status_code == 404


@pytest.mark.parametrize(
    ("body", "raises"),
    [
        # Nested past the decoder's recursion limit. No fixed depth is past it
        # on every run, so ``None`` stands for a body found on the thread that
        # decodes it, under the 1 MiB default cap: see ``tests.utils``.
        pytest.param(None, RecursionError, id="nested-past-the-recursion-limit"),
        # Over the 4300-digit cap on int conversion: a plain ``ValueError``.
        pytest.param(
            b'{"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"n": ' + b"9" * 5000 + b"}}",
            ValueError,
            id="integer-over-the-digit-limit",
        ),
        # Not UTF-8: a ``UnicodeDecodeError`` from decoding the bytes.
        pytest.param(
            b'{"jsonrpc": "2.0", "id": 1, "method": "\xff"}',
            UnicodeDecodeError,
            id="invalid-utf-8",
        ),
    ],
)
def test_async_body_json_cannot_decode_is_a_parse_error(
    async_urlconf, body: bytes | None, raises: type[Exception]
) -> None:
    """None of these is a ``JSONDecodeError``, and each escaped as a 500.

    The parse runs before authentication, so anyone could send one. The id is
    ``null`` because a body that never decoded has no id to echo. A sync test
    on purpose: the async view parses on the thread running its event loop, so
    the loop is started on the bounded stack rather than on pytest's, and the
    nested body is found on that thread because the bound is a floor rather than
    a size.
    """

    async def post(sent: bytes) -> Any:
        return await AsyncClient().post(
            "/mcp/",
            data=sent,
            content_type="application/json",
            headers={"Mcp-Protocol-Version": "2025-11-25"},
        )

    def run() -> Any:
        sent: bytes = body if body is not None else nested_past_the_decoders_limit_here()
        # Checked on the thread that decodes it, because that thread's stack
        # is what the nested body's limit depends on. A body that decoded here
        # would be answered by a later check and pass without the fix.
        with pytest.raises(raises) as raised:
            json.loads(sent)
        assert raised.type is raises
        return asyncio.run(post(sent))

    response = on_a_bounded_stack(run)
    payload: Any = response.json()
    # Neither the size cap, which runs before the parse, nor the shape check
    # a body that decoded would reach after it.
    assert payload["error"]["message"] != "Request body too large"
    assert payload["error"]["message"] != "JSON-RPC message must be a JSON object"
    assert response.status_code == 400
    assert payload == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": "Invalid JSON: body could not be decoded"},
    }


async def test_async_get_missing_protocol_version_allowed_when_disabled() -> None:
    """The GET (SSE) path mirrors POST when the relaxation is enabled.

    With the flag off, a header-less GET would 400 on the version check. With it
    on, the request progresses past that check and 404s on the missing session
    id instead — which is what we assert here.
    """
    server = build_kernel_server(
        config=build_mcp_config(allowed_origins=["*"], require_protocol_version_header=False)
    )
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
        response = await AsyncClient().get("/mcp/")
    assert response.status_code == 404


async def test_acall_invokes_async_function_directly() -> None:
    """``acall`` skips the thread hop when the callable is already async."""
    from django_spec_mcp._compat.acall import acall

    async def aproduce(x: int) -> int:
        return x * 2

    assert await acall(aproduce, 21) == 42


async def test_acall_invokes_sync_function_via_thread() -> None:
    from django_spec_mcp._compat.acall import acall

    def produce(x: int) -> int:
        return x * 2

    assert await acall(produce, 21) == 42


# ----- middleware opt-out flags -----
#
# Django's ``CsrfViewMiddleware`` and ``LoginRequiredMiddleware`` read their
# opt-out flags off the *resolved* callable, which is the function ``as_view``
# returns. It has to carry them or POST/DELETE to the MCP endpoint 403s (no
# CSRF token) or 302s to a login page, neither of which a bearer-token
# transport can satisfy.

_CSRF_MIDDLEWARE = ["django.middleware.csrf.CsrfViewMiddleware"]


def _async_view():
    return AsyncStreamableHttpView.as_view(
        tools=ToolRegistry(),
        resources=ResourceRegistry(),
        session_store=InMemorySessionStore(),
    )


def test_async_view_is_csrf_exempt() -> None:
    assert _async_view().csrf_exempt is True


@pytest.mark.skipif(DJANGO_VERSION < (5, 1), reason="LoginRequiredMiddleware lands in Django 5.1")
def test_async_view_opts_out_of_login_required() -> None:
    assert _async_view().login_required is False


def test_async_view_names_its_class() -> None:
    """Django's ``view_class`` names the class; the DRF-style ``cls`` and ``actions`` are gone."""
    view = _async_view()
    assert view.view_class is AsyncStreamableHttpView
    assert not hasattr(view, "cls")
    assert not hasattr(view, "actions")
    # No wrapper sits between Django and the view any more, so there is
    # nothing for ``__wrapped__`` to point introspection at.
    assert not hasattr(view, "__wrapped__")


def test_async_view_is_a_coroutine_function() -> None:
    """Django awaits the view because ``as_view`` marked it, with no thread hop."""
    assert iscoroutinefunction(_async_view())


async def test_async_post_passes_csrf_middleware(async_urlconf) -> None:
    with override_settings(MIDDLEWARE=_CSRF_MIDDLEWARE):
        client = AsyncClient(enforce_csrf_checks=True)
        sid = await _initialize(client)
    assert sid


async def test_async_delete_passes_csrf_middleware(async_urlconf) -> None:
    client = AsyncClient(enforce_csrf_checks=True)
    sid = await _initialize(client)
    with override_settings(MIDDLEWARE=_CSRF_MIDDLEWARE):
        response = await client.delete(
            "/mcp/",
            headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
        )
    assert response.status_code == 204


class _Deny(PermissionCheck):
    def has_permission(self, *args: Any, **kwargs: Any) -> bool:
        return False


def _gated_server() -> MCPServer:
    """A tool behind a scope the ``AllowAnyBackend`` token lacks, and one its spec denies.

    ``AllowAnyBackend`` authenticates the request but issues no scopes, so the
    first denial is a *scope* failure, the branch that carries ``scope=`` in
    the challenge. The second is the spec's own check, which names no scope.
    """
    server = MCPServer(
        name="async-gated", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    server.register_tool(
        ServiceSpec(service=lambda: {}, permissions=[Unrestricted()]),
        name="gated",
        description="Needs a scope the AllowAnyBackend token does not carry.",
        scopes="invoices:write",
    )
    server.register_tool(
        ServiceSpec(service=lambda: {}, permissions=[_Deny()]),
        name="denied",
        description="Denied by its spec's own check.",
    )
    return server


async def _call_gated(name: str) -> Any:
    with override_settings(ROOT_URLCONF=urlconf_for(_gated_server(), is_async=True)):
        client = AsyncClient()
        sid = await _initialize(client)
        return await client.post(
            "/mcp/",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": {}},
                }
            ),
            content_type="application/json",
            headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
        )


@pytest.mark.django_db(transaction=True)
async def test_async_permission_denial_is_403_with_a_scope_challenge() -> None:
    """The async transport maps a denial the same way the sync one does.

    Added because the 403 mapping landed in both viewsets but only the sync
    path had a denial test: the async branch was live and uncovered, which
    is how the two paths drift.
    """
    response = await _call_gated("gated")
    assert response.status_code == 403, response.content
    assert response.json()["error"]["data"]["requiredScopes"] == ["invoices:write"]
    challenge = response["WWW-Authenticate"]
    assert 'error="insufficient_scope"' in challenge
    assert 'scope="invoices:write"' in challenge


@pytest.mark.django_db(transaction=True)
async def test_async_spec_check_denial_is_403_with_a_plain_challenge() -> None:
    """The kernel's ``NotPermitted`` reaches the wire as the scope gate's denial
    does, a ``403`` with ``-32006``, and its challenge names no scope, since the
    check asked for none."""
    response = await _call_gated("denied")
    assert response.status_code == 403, response.content
    assert response.json()["error"] == {"code": -32006, "message": "Insufficient permission"}
    assert "scope=" not in response["WWW-Authenticate"]
