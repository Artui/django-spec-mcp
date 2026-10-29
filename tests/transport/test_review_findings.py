"""Regressions for the five defects the PR review found.

Each was reachable on a supported configuration and each passed CI at 100%
coverage — they were unexercised *combinations*, not uncovered lines, which is
exactly the gap a coverage number cannot see. Grouped in one file so the
provenance stays obvious.

The two that registered a tool or a resource now ask it of kernel specs
registered with ``register_tool`` and ``register_resource``. A kernel resource
has two gates: its ``scopes=`` and its spec's own checks, so the denied read is
asked of each.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, override_settings
from django_service_specs import PermissionCheck, SelectorKind, SelectorSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.handle_initialize import handle_initialize
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.transport.negotiate_protocol_version import negotiate_protocol_version

MODERN = "2026-07-28"
LEGACY = "2025-11-25"


# ----- 1. a modern-only PROTOCOL_VERSIONS was a 500 -----


def _modern_only() -> Any:
    return build_mcp_config(protocol_versions=["2026-07-28"])


def test_a_modern_only_server_has_no_legacy_fallback() -> None:
    config = _modern_only()
    assert config.legacy_protocol_versions == ()
    assert config.legacy_fallback_version is None


def test_discovery_still_works_on_a_modern_only_server() -> None:
    """This used to raise ``IndexError`` → HTTP 500.

    ``server/discover`` is the request a modern client leads with, sent without
    a version header precisely because it is asking which versions exist.
    Refusing it would leave the server undiscoverable by exactly the clients it
    still serves — so the version falls back to the head of the list whatever
    era that is.
    """
    assert negotiate_protocol_version(None, config=_modern_only(), is_sessionless=True) == MODERN


def test_a_legacy_client_mid_session_is_rejected_not_answered_with_a_modern_version() -> None:
    """The other half of the same branch, and it must not go the same way:
    handing a modern version to a header-less legacy request would tell it to
    speak a revision it cannot."""
    config = build_mcp_config(
        protocol_versions=["2026-07-28"], require_protocol_version_header=False
    )
    assert negotiate_protocol_version(None, config=config, is_sessionless=False) is None


def test_initialize_on_a_modern_only_server_explains_itself(rf: Any) -> None:
    """Also an ``IndexError`` → 500 before. A legacy client learns the
    handshake era is gone and what replaced it — something it can report to a
    human, which a 500 is not."""
    server = MCPServer(
        name="modern-only",
        auth_backend=AllowAnyBackend(),
        config=_modern_only(),
    )
    result = handle_initialize(
        {"protocolVersion": LEGACY, "capabilities": {}},
        server._call_context(user=None),
    )
    assert isinstance(result, JsonRpcError)
    assert "no longer serves the initialize handshake" in result.message
    assert MODERN in result.message


def test_a_server_supporting_no_revision_at_all_is_refused_at_construction() -> None:
    """The one genuinely unusable value — caught once, at startup, rather than
    as an index error out of a view on the first request."""
    with pytest.raises(ImproperlyConfigured, match="supports no MCP revision"):
        build_mcp_config(protocol_versions=[])


# ----- 2 & 3. streaming only where a reporter exists -----


def _selector() -> list[dict[str, str]]:
    return [{"ok": "1"}]


def _thing() -> dict[str, str]:
    return {"ok": "1"}


class _Denies(PermissionCheck):
    def has_permission(self, *args: Any, **kwargs: Any) -> bool:
        return False


def _stream_server() -> MCPServer:
    server = MCPServer(
        name="stream-gate",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
    )
    server.register_tool(
        SelectorSpec(kind=SelectorKind.LIST, selector=_selector, permissions=[Unrestricted()]),
        name="s.list",
        description="x",
        paginate=True,
    )
    server.register_resource(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_thing, permissions=[Unrestricted()]),
        name="scoped",
        uri_template="scoped://thing",
        scopes="things:read",
    )
    server.register_resource(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_thing, permissions=[_Denies()]),
        name="denied",
        uri_template="denied://thing",
    )
    return server


@pytest.fixture
def stream_urls() -> Any:
    import types

    from django.urls import path

    module = types.ModuleType("tests.transport._stream_gate_urls")
    module.urlpatterns = [path("mcp/", _stream_server().async_urls)]  # type: ignore[attr-defined]
    return module


def _post(client: Client, method: str, params: dict[str, Any]) -> Any:
    body = dict(params)
    body["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientCapabilities": {},
        "progressToken": "tok",
    }
    headers = {"Mcp-Method": method, "Mcp-Protocol-Version": MODERN}
    name = params.get("name") or params.get("uri")
    if name is not None:
        headers["Mcp-Name"] = name
    return client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": body}),
        content_type="application/json",
        headers=headers,
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("uri", ["scoped://thing", "denied://thing"])
def test_a_denied_resource_read_keeps_its_403_even_with_a_progress_token(
    stream_urls: Any, uri: str
) -> None:
    """**The security-relevant one.** ``resources/read`` runs a permission
    stack but the pre-flight could only speak for ``tools/call``, so a denial
    rode inside a ``200`` SSE body with no ``WWW-Authenticate``, and a client
    acting on status parsed the denial as success. Asked of the scope gate and
    of the spec's own check."""
    with override_settings(ROOT_URLCONF=stream_urls):
        response = Client().post(
            "/mcp/",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "resources/read",
                    "params": {
                        "uri": uri,
                        "_meta": {
                            "io.modelcontextprotocol/protocolVersion": MODERN,
                            "io.modelcontextprotocol/clientCapabilities": {},
                            "progressToken": "tok",
                        },
                    },
                }
            ),
            content_type="application/json",
            headers={
                "Mcp-Method": "resources/read",
                "Mcp-Protocol-Version": MODERN,
                "Mcp-Name": uri,
            },
        )
    assert response.status_code == 403
    assert "WWW-Authenticate" in response.headers
    assert response.headers["Content-Type"] != "text/event-stream"
    assert json.loads(response.content)["error"]["code"] == -32006


@pytest.mark.django_db(transaction=True)
def test_a_selector_tool_still_streams(stream_urls: Any) -> None:
    """The gate must not close on the paths that *can* report — that would
    silently remove the feature it exists to protect."""
    with override_settings(ROOT_URLCONF=stream_urls):
        response = _post(Client(), "tools/call", {"name": "s.list", "arguments": {}})
    assert response.headers["Content-Type"].startswith("text/event-stream")


@pytest.mark.django_db(transaction=True)
def test_an_unknown_tool_is_not_streamed(stream_urls: Any) -> None:
    """No binding, so nothing can report and nothing can be pre-flighted; the
    handler's ``-32602`` is the useful answer."""
    with override_settings(ROOT_URLCONF=stream_urls):
        response = _post(Client(), "tools/call", {"name": "nope", "arguments": {}})
    assert response.headers["Content-Type"].startswith("application/json")
    assert json.loads(response.content)["error"]["code"] == -32602


@pytest.mark.django_db(transaction=True)
def test_a_tools_call_with_a_non_string_name_is_not_streamed(stream_urls: Any) -> None:
    with override_settings(ROOT_URLCONF=stream_urls):
        response = _post(Client(), "tools/call", {"name": 7, "arguments": {}})
    assert response.headers["Content-Type"].startswith("application/json")


# ----- 5. the reporter's counters -----


def test_the_reporter_guards_its_counters_with_a_lock() -> None:
    """The frame hand-off was thread-safe; the cap and the monotonicity check
    were not. Safe today only because a collaborator happens to use one
    thread — which is not a property of this class."""
    import asyncio

    from django_spec_mcp.transport.response_stream import _StreamReporter

    async def _build() -> Any:
        return _StreamReporter(
            loop=asyncio.get_running_loop(),
            queue=asyncio.Queue(),
            token="t",
            remaining=1,
        )

    reporter = asyncio.run(_build())
    assert reporter._lock is not None


# ----- smaller note: a tuple of resource links -----


def test_a_tuple_of_resource_links_is_accepted() -> None:
    """A selector returning a tuple is producing the right shape; rejecting it
    with a message about the wrong shape read as nonsense."""
    from django_spec_mcp.output.build_content_blocks import build_content_blocks
    from django_spec_mcp.types.tool_content_kind import ToolContentKind

    blocks = build_content_blocks(
        ({"uri": "a://1", "name": "one"}, {"uri": "a://2", "name": "two"}),
        content_kind=ToolContentKind.RESOURCE_LINK,
        mime_type=None,
    )
    assert isinstance(blocks, list)
    assert len(blocks) == 2
