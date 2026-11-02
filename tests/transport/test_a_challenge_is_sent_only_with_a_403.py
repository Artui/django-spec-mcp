"""``WWW-Authenticate`` rides a ``403`` and no other JSON-RPC error answer.

The challenge names the scopes a denied caller must ask for. A client that
meets it on an unknown method or an in-band error would read "ask for more
scope" where nothing about its token is wrong, so ``challenged_error_response``
adds it for the permission denial's status alone.
"""

from __future__ import annotations

from typing import Any

from tests.conformance.mcp import build_conformance_server
from tests.transport.conftest import Endpoint

_MODERN_VERSION: str = "2026-07-28"
_META: dict[str, Any] = {
    "io.modelcontextprotocol/protocolVersion": _MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0.0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


def _modern(method: str, params: dict[str, Any], **headers: str) -> dict[str, Any]:
    """A modern-era request's body and headers, as ``Endpoint.post`` takes them."""
    return {
        "body": {
            "jsonrpc": "2.0",
            "id": 7,
            "method": method,
            "params": {**params, "_meta": _META},
        },
        "headers": {"Mcp-Protocol-Version": _MODERN_VERSION, "Mcp-Method": method, **headers},
    }


async def _legacy_call(endpoint: Endpoint, method: str, params: dict[str, Any]) -> Any:
    """``method`` on a freshly opened legacy session, which answers in band."""
    session_id: str = await endpoint.initialize()
    return await endpoint.post(
        {"jsonrpc": "2.0", "id": 7, "method": method, "params": params}, session_id=session_id
    )


async def test_a_modern_denial_is_a_403_with_a_challenge(endpoint: Endpoint) -> None:
    with endpoint.mount(build_conformance_server()):
        sent = _modern(
            "tools/call",
            {"name": "conformance.gated", "arguments": {}},
            **{"Mcp-Name": "conformance.gated"},
        )
        response = await endpoint.post(sent["body"], **sent["headers"])
    assert response.status_code == 403, response.content
    assert response["WWW-Authenticate"].startswith("Bearer")


async def test_a_legacy_denial_is_a_403_with_a_challenge(endpoint: Endpoint) -> None:
    with endpoint.mount(build_conformance_server()):
        response = await _legacy_call(
            endpoint, "tools/call", {"name": "conformance.gated", "arguments": {}}
        )
    assert response.status_code == 403, response.content
    assert response["WWW-Authenticate"].startswith("Bearer")


async def test_a_modern_unknown_method_is_a_404_with_no_challenge(endpoint: Endpoint) -> None:
    sent = _modern("nonsense/method", {})
    response = await endpoint.post(sent["body"], **sent["headers"])
    assert response.status_code == 404, response.content
    assert response.json()["error"]["code"] == -32601
    assert "WWW-Authenticate" not in response


async def test_a_legacy_error_inside_a_200_carries_no_challenge(endpoint: Endpoint) -> None:
    response = await _legacy_call(endpoint, "nonsense/method", {})
    assert response.status_code == 200, response.content
    assert response.json()["error"]["code"] == -32601
    assert "WWW-Authenticate" not in response
