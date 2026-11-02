"""The ``400`` for an unsupported protocol version echoes the request's ``id``, in both eras.

A client matching responses to requests by ``id`` cannot place an error whose
``id`` is null. Both views answer a legacy request's ``400`` from their own call
site, so the test runs on each. A modern request's comes from the header check
both views share (``modern_header_refusal``), which answers every modern header
refusal, so it runs on each view too.
"""

from __future__ import annotations

from django_spec_mcp.types.protocol_version_meta_key import PROTOCOL_VERSION_META_KEY
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


async def test_a_modern_requests_version_400_carries_its_id(endpoint: Endpoint) -> None:
    response = await endpoint.post(
        {
            "jsonrpc": "2.0",
            "id": 4242,
            "method": "tools/list",
            "params": {"_meta": {PROTOCOL_VERSION_META_KEY: "1999-01-01"}},
        },
        **{"Mcp-Protocol-Version": "1999-01-01"},
    )
    assert response.status_code == 400, response.content
    body = response.json()
    assert body["error"]["code"] == -32022
    assert body["id"] == 4242
