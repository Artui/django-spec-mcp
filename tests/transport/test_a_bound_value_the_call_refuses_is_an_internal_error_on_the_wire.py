"""What an HTTP client sees when a bound value fails the call's own declaration.

``tests/handlers/test_a_tool_call_reads_the_spec_under_its_call_context.py``
holds that the handler raises ``ImproperlyConfigured`` for it. The raise is the
server's mistake, so on the wire it is the dispatch backstop's ``500`` with a
JSON-RPC ``-32603``, never a ``200`` carrying an ``isError`` ``validation_error``
tool result, which would tell the caller an argument it never sent is invalid.
Each case posts through a real URL conf, on the sync and the async view, plain
and with a ``progressToken``. The async view's streamed call fails in its
pre-flight, before a stream opens, so it answers the same JSON rather than a
stream; the sync view never streams, so its ``progressToken`` case pins that a
token changes nothing there.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.test import AsyncClient, Client, override_settings
from django_service_specs import ServerArguments, ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import TypedByTheCall, make_server

MODERN = "2026-07-28"
# A string id, so a reply echoing a default ``1`` or ``null`` cannot pass.
REQUEST_ID = "bound-3"
TOOL: str = "projects.touch"

_VIEW_LOGGERS: dict[bool, str] = {
    False: "django_spec_mcp.transport.streamable_http_view",
    True: "django_spec_mcp.transport.async_streamable_http_view",
}


def _touch(*, project: Any) -> dict[str, Any]:
    return {"project": project}


def _server() -> MCPServer:
    """``"x"`` is bound for ``project``: it passes the declaration made to no call, which
    registration reads, and the declaration made to a call of ``TOOL`` refuses it."""
    server = make_server(session_store=InMemorySessionStore())
    server.register_tool(
        ServiceSpec(
            service=_touch,
            validator=TypedByTheCall(TOOL),
            permissions=[Unrestricted()],
            atomic=False,
        ),
        name=TOOL,
        server_arguments=ServerArguments(values={"project": "x"}),
    )
    return server


def _request(*, progress: bool) -> tuple[str, dict[str, str]]:
    meta: dict[str, Any] = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    if progress:
        meta["progressToken"] = "p1"
    body = {
        "jsonrpc": "2.0",
        "id": REQUEST_ID,
        "method": "tools/call",
        "params": {"name": TOOL, "arguments": {}, "_meta": meta},
    }
    headers = {"Mcp-Protocol-Version": MODERN, "Mcp-Method": "tools/call", "Mcp-Name": TOOL}
    return json.dumps(body), headers


def _post_sync(server: MCPServer, *, progress: bool) -> Any:
    body, headers = _request(progress=progress)
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        return Client(raise_request_exception=False).post(
            "/mcp/", data=body, content_type="application/json", headers=headers
        )


async def _post_async(server: MCPServer, *, progress: bool) -> Any:
    body, headers = _request(progress=progress)
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
        return await AsyncClient(raise_request_exception=False).post(
            "/mcp/", data=body, content_type="application/json", headers=headers
        )


@pytest.mark.parametrize("progress", [False, True], ids=["plain", "streamed"])
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_a_bound_value_the_call_refuses_is_a_json_rpc_internal_error(
    is_async: bool, progress: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """``500`` and ``-32603`` saying nothing more, no tool result, the fault logged.

    Before 0.8.1 the bound value was checked against the declaration made to no
    call, reached dispatch, and came back as a ``200`` whose tool result was a
    ``validation_error`` naming ``project``, an argument the caller never sent.
    """
    caplog.set_level(logging.ERROR, logger="django_spec_mcp")
    if is_async:
        response = await _post_async(_server(), progress=progress)
    else:
        # Off the loop: the sync test client must not run inside an event loop.
        response = await sync_to_async(_post_sync)(_server(), progress=progress)

    assert not response.streaming
    assert response["Content-Type"].startswith("application/json"), response.content[:200]
    assert response.status_code == 500, response.content
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": REQUEST_ID,
        "error": {"code": -32603, "message": "Internal error"},
    }
    text = response.content.decode()
    for tool_result_marker in ("isError", "validation_error", "project", "refuses"):
        assert tool_result_marker not in text

    records = [
        r
        for r in caplog.records
        if r.name == _VIEW_LOGGERS[is_async] and r.levelno >= logging.ERROR
    ]
    assert len(records) == 1, [r.name for r in caplog.records]
    assert records[0].exc_info is not None
    assert isinstance(records[0].exc_info[1], ImproperlyConfigured)
    # The kernel's rule, not any ``ImproperlyConfigured`` raised on the way to it.
    assert "binds a value its parameter refuses" in str(records[0].exc_info[1])
