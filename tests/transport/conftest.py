"""The ``endpoint`` fixture: one test body run against both streamable-HTTP views.

The sync and async views answer POST and DELETE identically, so a check of
that wire is written once and run on each mount rather than copied per view.
What only one view does (the async GET stream, the sync view's refusal of an
awaitable collaborator) keeps its own test.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.test import AsyncClient, Client, override_settings

from django_spec_mcp import MCPServer
from tests.testapp.mcp import build_server
from tests.testapp.urlconf_for import urlconf_for

LEGACY_VERSION: str = "2025-11-25"


def initialize_body(request_id: int = 1) -> str:
    """A legacy ``initialize`` request, as a client opening a session sends it."""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": LEGACY_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0.0"},
            },
        }
    )


@dataclass(frozen=True)
class Endpoint:
    """One mount of the MCP endpoint at ``/mcp/``, sync or async."""

    is_async: bool

    def mount(self, server: MCPServer) -> AbstractContextManager[Any]:
        """Serve ``server`` on this endpoint's kind of view for a ``with`` block."""
        return override_settings(ROOT_URLCONF=urlconf_for(server, is_async=self.is_async))

    async def send(self, method: str, path: str = "/mcp/", **kwargs: Any) -> Any:
        """Send one request on the mount's own client, the sync one off the event loop.

        Request headers go through ``headers=`` only: the async client puts an
        ``HTTP_*`` keyword in the ASGI scope rather than the request's headers,
        so a test written that way would pass without sending what it names.
        """
        if self.is_async:
            return await getattr(AsyncClient(), method)(path, **kwargs)
        return await sync_to_async(getattr(Client(), method))(path, **kwargs)

    async def post(self, body: Any, *, session_id: str | None = None, **headers: str) -> Any:
        """POST ``body`` (encoded unless already ``bytes`` or ``str``) as JSON-RPC."""
        sent: Any = body if isinstance(body, (bytes, str)) else json.dumps(body)
        sent_headers: dict[str, str] = {"Mcp-Protocol-Version": LEGACY_VERSION, **headers}
        if session_id is not None:
            sent_headers["Mcp-Session-Id"] = session_id
        return await self.send(
            "post", data=sent, content_type="application/json", headers=sent_headers
        )

    async def initialize(self) -> str:
        """Open a legacy session and return its id."""
        response = await self.send("post", data=initialize_body(), content_type="application/json")
        assert response.status_code == 200, response.content
        return response["Mcp-Session-Id"]


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def endpoint(request: pytest.FixtureRequest) -> Iterator[Endpoint]:
    """The test server on the sync and then the async view."""
    mounted = Endpoint(is_async=request.param)
    with mounted.mount(build_server()):
        yield mounted
