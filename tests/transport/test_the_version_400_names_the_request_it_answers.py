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
