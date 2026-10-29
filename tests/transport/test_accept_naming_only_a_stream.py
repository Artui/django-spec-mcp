"""An ``Accept`` naming only ``text/event-stream`` reaches the async endpoint.

The spec tells a client opening the GET stream to send exactly that header,
and the async endpoint answers with server-sent events on GET, on a
subscription and on a streamed call. Negotiating against JSON alone would make
this request a ``406``, and the GET stream could then be opened only by a
client sending an ``Accept`` the spec does not ask for. The sync endpoint never
streams: a GET or HEAD there is its
bodyless ``405``, which the spec allows a server offering no stream, whatever
the ``Accept``, while a POST asking for nothing but a stream is still refused
(``test_the_sync_endpoint_refuses_an_accept_naming_only_a_stream``).
"""

from __future__ import annotations

import json

import pytest
from asgiref.sync import sync_to_async
from django.test import AsyncClient, Client, override_settings

from tests.testapp.mcp import build_server
from tests.testapp.urlconf_for import urlconf_for

_STREAM_ONLY: dict[str, str] = {"Accept": "text/event-stream"}


def _initialize_body() -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "stream-only", "version": "1"},
            },
        }
    )


async def test_an_accept_naming_only_a_stream_reaches_the_async_endpoint() -> None:
    with override_settings(ROOT_URLCONF=urlconf_for(build_server(), is_async=True)):
        client = AsyncClient()
        initialized = await client.post(
            "/mcp/", _initialize_body(), content_type="application/json", headers=_STREAM_ONLY
        )
        assert initialized.status_code == 200, initialized.content
        assert initialized["Content-Type"] == "application/json"
        response = await client.get(
            "/mcp/",
            headers={
                **_STREAM_ONLY,
                "Mcp-Protocol-Version": "2025-11-25",
                "Mcp-Session-Id": initialized["Mcp-Session-Id"],
            },
        )
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/event-stream")
    # Drain a single chunk so the streaming generator unwinds cleanly.
    streaming = response.streaming_content
    assert await streaming.__anext__() == b": stream open\n\n"
    await streaming.aclose()


@pytest.mark.parametrize("method", ["get", "head"])
async def test_a_sync_get_naming_only_a_stream_is_the_405_not_a_406(method: str) -> None:
    """The spec has a GET list ``text/event-stream``; the answer is stream or ``405``."""
    with override_settings(ROOT_URLCONF=urlconf_for(build_server())):
        response = await sync_to_async(getattr(Client(), method))(
            "/mcp/", headers={**_STREAM_ONLY, "Mcp-Protocol-Version": "2025-11-25"}
        )
    assert response.status_code == 405, response.content
    assert response["Allow"] == "GET, POST, DELETE, HEAD, OPTIONS"
    assert response.content == b""
