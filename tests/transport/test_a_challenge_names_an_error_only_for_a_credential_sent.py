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
from tests.transport.conftest import Endpoint

_LIST: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


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


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    ("headers", "challenge"),
    [
        ({}, 'Bearer realm="mcp"'),
        ({"Authorization": "Bearer stale"}, 'Bearer realm="mcp", error="invalid_token"'),
    ],
    ids=["no-credential", "rejected-credential"],
)
@pytest.mark.parametrize("method", ["post", "delete"])
async def test_a_challenge_names_an_error_only_for_a_credential_sent(
    is_async: bool, headers: dict[str, str], challenge: str, method: str
) -> None:
    endpoint = Endpoint(is_async=is_async)
    server = MCPServer(name="t", auth_backend=_OneTokenBackend(), session_store=None)
    with endpoint.mount(server):
        if method == "post":
            response = await endpoint.post(_LIST, **headers)
        else:
            response = await endpoint.send(
                "delete",
                headers={"Mcp-Session-Id": "s", "Mcp-Protocol-Version": "2025-11-25", **headers},
            )
    assert response.status_code == 401
    assert response["WWW-Authenticate"] == challenge
