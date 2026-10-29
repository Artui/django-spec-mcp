"""A 401's challenge carries ``error="invalid_token"`` only when a credential was sent.

RFC 6750 3.1: when a request lacks any authentication information, the
challenge SHOULD NOT include an error code. A client meeting
``invalid_token`` on its first, credential-free request reads it as "the token
you sent is bad" and may discard a token it never presented, where the bare
challenge tells it to start the authorization flow.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.bearer_challenge import bearer_challenge
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.transport.in_memory_sse_broker import InMemorySSEBroker
from tests.transport.conftest import LEGACY_VERSION, Endpoint

_MODERN_VERSION: str = "2026-07-28"
_LIST: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
_MODERN_LIST: dict[str, Any] = {
    **_LIST,
    "params": {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": _MODERN_VERSION,
            "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0.0"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    },
}
_SESSION: dict[str, str] = {"Mcp-Session-Id": "s", "Mcp-Protocol-Version": LEGACY_VERSION}


class _OneTokenBackend:
    """Admits ``Bearer good`` and nothing else, challenging as a real backend does."""

    def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        if request.headers.get("Authorization") == "Bearer good":
            return TokenInfo(user=None)
        return None

    def protected_resource_metadata(self) -> dict:
        return {}

    def www_authenticate_challenge(
        self, *, scopes: list[str] | None = None, error: str | None = None
    ) -> str:
        return bearer_challenge(resource_metadata_url=None, scopes=scopes, error=error)


async def _refuse(endpoint: Endpoint, route: str, headers: dict[str, str]) -> Any:
    """Send ``route``'s request, each of which reaches its view's 401 call site."""
    if route == "legacy-post":
        return await endpoint.post(_LIST, **headers)
    if route == "modern-post":
        return await endpoint.post(
            _MODERN_LIST,
            **{"Mcp-Protocol-Version": _MODERN_VERSION, "Mcp-Method": "tools/list", **headers},
        )
    return await endpoint.send(route, headers={**_SESSION, **headers})


@pytest.mark.parametrize(
    ("is_async", "route"),
    [
        (False, "legacy-post"),
        (True, "legacy-post"),
        (False, "modern-post"),
        (True, "modern-post"),
        (False, "delete"),
        (True, "delete"),
        # The sync view's GET is a 405 before it authenticates; only the
        # async view, with a broker and sessions, reaches a challenge there.
        (True, "get"),
    ],
)
@pytest.mark.parametrize(
    ("headers", "challenge"),
    [
        ({}, 'Bearer realm="mcp"'),
        ({"Authorization": "Bearer stale"}, 'Bearer realm="mcp", error="invalid_token"'),
    ],
    ids=["no-credential", "rejected-credential"],
)
async def test_a_challenge_names_an_error_only_for_a_credential_sent(
    is_async: bool, route: str, headers: dict[str, str], challenge: str
) -> None:
    endpoint = Endpoint(is_async=is_async)
    server = MCPServer(
        name="t",
        auth_backend=_OneTokenBackend(),
        session_store=InMemorySessionStore(),
        sse_broker=InMemorySSEBroker(),
    )
    with endpoint.mount(server):
        response = await _refuse(endpoint, route, headers)
    assert response.status_code == 401
    assert response["WWW-Authenticate"] == challenge
