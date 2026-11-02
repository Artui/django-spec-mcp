"""The ``400`` for an unsupported ``Mcp-Protocol-Version`` echoes the request's ``id``.

A client matching responses to requests by ``id`` cannot place an error whose
``id`` is null. Both views answer this ``400`` from their own call site, so the
test runs on each.
"""

from __future__ import annotations

from tests.transport.conftest import Endpoint


async def test_the_version_400_carries_the_requests_id(endpoint: Endpoint) -> None:
    response = await endpoint.post(
        {"jsonrpc": "2.0", "id": 4242, "method": "tools/list"},
        **{"Mcp-Protocol-Version": "1999-01-01"},
    )
    assert response.status_code == 400, response.content
    body = response.json()
    assert body["error"]["message"] == "Missing or unsupported MCP-Protocol-Version"
    assert body["id"] == 4242
