"""A server's ``conventions=`` reaches the wire through both views, in both eras.

Over HTTP the handlers never see the server: each view builds the request's
context from what ``MCPServer`` handed ``as_view``, once per view, and each
protocol era reaches that build by its own path. A convention dropped on any of
those four routes leaves it speaking the defaults while ``call_tool`` and
``list_tools`` speak the server's, which is why each view is driven here in both
eras rather than once.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from django_spec_mcp import AgentConventions
from tests.testapp.conventions import conventions_server
from tests.transport.conftest import Endpoint
from tests.utils import tool_error

LEGACY = "2025-11-25"
MODERN = "2026-07-28"

CONVENTIONS = AgentConventions(
    handle_field_description="Wire handle.",
    handle_line="Wire line.",
    query_param_on_pages="Wire scope.",
    missing_arguments="Wire missing: {names}.",
)


def _request(
    era: str, method: str, params: dict[str, Any], session_id: str | None
) -> dict[str, Any]:
    """The keyword arguments of a POST carrying ``method`` in ``era``."""
    headers: dict[str, str] = {"Mcp-Protocol-Version": era}
    if era == MODERN:
        params = {
            **params,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MODERN,
                "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0.0"},
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        }
        headers["Mcp-Method"] = method
        if "name" in params:
            headers["Mcp-Name"] = params["name"]
    else:
        assert session_id is not None
        headers["Mcp-Session-Id"] = session_id
    return {
        "data": json.dumps({"jsonrpc": "2.0", "id": 2, "method": method, "params": params}),
        "content_type": "application/json",
        "headers": headers,
    }


_MISSING_PK: dict[str, Any] = {"name": "invoices.rename", "arguments": {"number": "X"}}


def _assert_served_with_the_conventions(listed: Any, called: Any) -> None:
    assert listed.status_code == 200, listed.content
    tools = {tool["name"]: tool for tool in listed.json()["result"]["tools"]}
    rename = tools["invoices.rename"]
    assert rename["description"] == "Rename an invoice.\n\nWire line."
    assert rename["outputSchema"]["properties"]["id"]["description"] == "Wire handle."
    fields = tools["invoices.list"]["inputSchema"]["properties"]["fields"]
    assert fields["description"] == "Fields to return. Wire scope."
    assert called.status_code == 200, called.content
    assert tool_error(called.json()["result"])["message"] == "Wire missing: `pk`."


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("era", [LEGACY, MODERN])
async def test_each_view_serves_the_servers_conventions(endpoint: Endpoint, era: str) -> None:
    with endpoint.mount(conventions_server(CONVENTIONS)):
        session_id: str | None = None
        if era == LEGACY:
            session_id = await endpoint.initialize()
        listed = await endpoint.send("post", **_request(era, "tools/list", {}, session_id))
        called = await endpoint.send("post", **_request(era, "tools/call", _MISSING_PK, session_id))

    _assert_served_with_the_conventions(listed, called)
