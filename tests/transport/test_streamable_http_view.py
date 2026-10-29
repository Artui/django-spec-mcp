"""What both streamable-HTTP views answer a POST or DELETE with, before and around dispatch.

Each test runs once per view through the ``endpoint`` fixture, because the two
views answer these requests identically. ``test_a_body_json_cannot_decode_is_a_parse_error``
is the sync view's alone: it must decode on a bounded stack, and its async twin
lives with the async view's tests.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import Client, RequestFactory, override_settings

from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.registry.resource_registry import ResourceRegistry
from django_spec_mcp.registry.tool_registry import ToolRegistry
from django_spec_mcp.transport.async_streamable_http_view import AsyncStreamableHttpView
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.transport.streamable_http_view import StreamableHttpView
from tests.testapp.mcp import build_server
from tests.testapp.urlconf_for import urlconf_for
from tests.transport.conftest import Endpoint, initialize_body
from tests.utils import nested_past_the_decoders_limit_here, on_a_bounded_stack

# Scalars are resolved once, in ``MCPServer.__init__`` — so a test that needs
# non-default scalars mounts its own server rather than mutating settings around
# the shared one (which is built when its URL conf is first imported, and would
# both ignore the change and leak it into every later test in the process).


async def test_post_with_too_large_body(endpoint: Endpoint) -> None:
    server = build_server(config=build_mcp_config(allowed_origins=["*"], max_request_bytes=10))
    with endpoint.mount(server):
        response = await endpoint.post(b"X" * 1024)
    assert response.status_code == 413
    assert response.json()["error"]["message"] == "Request body too large"


async def test_a_decode_error_keeps_the_decoders_detail(endpoint: Endpoint) -> None:
    """A ``JSONDecodeError`` names what it expected, and the refusal carries it."""
    response = await endpoint.post("not json")
    assert response.status_code == 400
    assert response.json()["error"] == {"code": -32700, "message": "Invalid JSON: Expecting value"}


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
def test_a_body_json_cannot_decode_is_a_parse_error(
    client: Client, body: bytes | None, raises: type[Exception]
) -> None:
    """None of these is a ``JSONDecodeError``, and each escaped as a 500.

    The parse runs before authentication, so anyone could send one. The id is
    ``null`` because a body that never decoded has no id to echo. The request
    runs on a bounded stack, which keeps the nested body small, and that body is
    found on the same thread because the bound is a floor rather than a size.
    """

    def post() -> Any:
        sent: bytes = body if body is not None else nested_past_the_decoders_limit_here()
        # Checked on the thread that decodes it, because that thread's stack
        # is what the nested body's limit depends on. A body that decoded here
        # would be answered by a later check and pass without the fix.
        with pytest.raises(raises) as raised:
            json.loads(sent)
        assert raised.type is raises
        return client.post(
            "/mcp/",
            data=sent,
            content_type="application/json",
            HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
        )

    response = on_a_bounded_stack(post)
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


async def test_post_with_invalid_jsonrpc_shape(endpoint: Endpoint) -> None:
    response = await endpoint.post({"foo": "bar"})
    assert response.json()["error"]["code"] == -32600


@pytest.mark.parametrize("verb", ["post", "get", "delete"])
async def test_origin_not_allowed_returns_403(endpoint: Endpoint, verb: str) -> None:
    """Every method is refused for an origin outside the allowlist, before anything else."""
    server = build_server(config=build_mcp_config(allowed_origins=["https://allowed.example"]))
    with endpoint.mount(server):
        if verb == "post":
            response = await endpoint.post(
                {"jsonrpc": "2.0", "id": 1, "method": "ping"}, Origin="https://blocked.example"
            )
        else:
            response = await endpoint.send(verb, headers={"Origin": "https://blocked.example"})
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Origin not allowed: 'https://blocked.example'"


async def test_delete_without_session_id_is_204(endpoint: Endpoint) -> None:
    response = await endpoint.send("delete")
    assert response.status_code == 204


def test_two_servers_can_allow_different_origins(client: Client) -> None:
    """The payoff: an origin allowed by one mount is refused by the other."""
    internal = build_server(config=build_mcp_config(allowed_origins=["https://internal.example"]))
    public = build_server(config=build_mcp_config(allowed_origins=["https://public.example"]))

    with override_settings(ROOT_URLCONF=urlconf_for(internal)):
        allowed = client.get("/mcp/", HTTP_ORIGIN="https://internal.example")
    with override_settings(ROOT_URLCONF=urlconf_for(public)):
        refused = client.get("/mcp/", HTTP_ORIGIN="https://internal.example")

    assert allowed.status_code != 403
    assert refused.status_code == 403


async def test_post_response_jsonrpc_id_with_jsonrpc_request_only(endpoint: Endpoint) -> None:
    """A bare JSON-RPC response object posted to the server is rejected.

    Sent inside a session, so the refusal is the view's and not the session
    gate's, which answers ``-32600`` too.
    """
    session_id: str = await endpoint.initialize()
    response = await endpoint.post(
        {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}, session_id=session_id
    )
    assert response.status_code == 400
    assert response.json()["error"] == {"code": -32600, "message": "Expected a JSON-RPC request"}


async def test_post_with_list_params_treated_as_no_params(endpoint: Endpoint) -> None:
    """JSON-RPC list-shaped params are silently coerced to None for MCP methods."""
    session_id: str = await endpoint.initialize()
    response = await endpoint.post(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": [1, 2]},
        session_id=session_id,
    )
    assert "result" in response.json()


async def test_request_with_no_params_field(endpoint: Endpoint) -> None:
    """Omitting ``params`` entirely is valid — handler receives ``None``."""
    session_id: str = await endpoint.initialize()
    response = await endpoint.post(
        {"jsonrpc": "2.0", "id": 1, "method": "ping"}, session_id=session_id
    )
    assert response.json()["result"] == {"resultType": "complete"}


async def test_a_notification_returns_202(endpoint: Endpoint) -> None:
    session_id: str = await endpoint.initialize()
    response = await endpoint.post(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id=session_id
    )
    assert response.status_code == 202


async def test_dispatch_error_envelope(endpoint: Endpoint) -> None:
    """A handler returning a ``JsonRpcError`` is wrapped in an ``error`` envelope."""
    session_id: str = await endpoint.initialize()
    response = await endpoint.post(
        {
            "jsonrpc": "2.0",
            "id": 14,
            "method": "tools/call",
            "params": {"name": "does.not.exist", "arguments": {}},
        },
        session_id=session_id,
    )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32602


async def test_unknown_session_returns_404(endpoint: Endpoint) -> None:
    response = await endpoint.post(
        {"jsonrpc": "2.0", "id": 6, "method": "tools/list"}, session_id="not-real"
    )
    assert response.status_code == 404


async def test_missing_protocol_version(endpoint: Endpoint) -> None:
    response = await endpoint.send(
        "post",
        data=json.dumps({"jsonrpc": "2.0", "id": 8, "method": "tools/list"}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Missing or unsupported MCP-Protocol-Version"


async def test_post_missing_protocol_version_allowed_when_disabled(endpoint: Endpoint) -> None:
    server = build_server(
        config=build_mcp_config(allowed_origins=["*"], require_protocol_version_header=False)
    )
    with endpoint.mount(server):
        session_id: str = await endpoint.initialize()
        response = await endpoint.send(
            "post",
            data=json.dumps({"jsonrpc": "2.0", "id": 81, "method": "tools/list"}),
            content_type="application/json",
            headers={"Mcp-Session-Id": session_id},
        )
    assert response.status_code == 200
    assert "result" in response.json()


async def test_initialize_with_unsupported_protocol_header_is_rejected(endpoint: Endpoint) -> None:
    server = build_server(
        config=build_mcp_config(allowed_origins=["*"], protocol_versions=["2025-11-25"])
    )
    with endpoint.mount(server):
        response = await endpoint.send(
            "post",
            data=initialize_body(),
            content_type="application/json",
            headers={"Mcp-Protocol-Version": "9999-99-99"},
        )
    # ``initialize`` may omit the header; it may not name a version this
    # server does not speak. Falling back would answer a different protocol
    # than the one asked for and say nothing about it.
    assert response.status_code == 400


class _DenyAllBackend:
    """Auth backend that rejects every request — exercises the 401 path."""

    def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        return None

    def protected_resource_metadata(self) -> dict:
        return {}

    def www_authenticate_challenge(self, *, scopes=None, error=None) -> str:
        del scopes, error
        return 'Bearer realm="x", error="invalid_token"'


@pytest.mark.parametrize(
    "view_class", [StreamableHttpView, AsyncStreamableHttpView], ids=["sync", "async"]
)
async def test_unauthenticated_response_uses_backend_challenge(view_class: Any) -> None:
    """When the auth backend returns ``None``, the transport emits 401 + WWW-Authenticate."""
    request = RequestFactory().post(
        "/mcp/", data=initialize_body(), content_type="application/json"
    )
    view = view_class.as_view(
        tools=ToolRegistry(),
        resources=ResourceRegistry(),
        auth_backend=_DenyAllBackend(),
        session_store=InMemorySessionStore(),
        config=build_mcp_config(),
    )
    response = view(request)
    if inspect.isawaitable(response):
        response = await response
    assert response.status_code == 401
    assert response["WWW-Authenticate"] == 'Bearer realm="x", error="invalid_token"'
    assert json.loads(response.content) == {
        "error": "unauthorized",
        "error_description": "Authentication required.",
    }
