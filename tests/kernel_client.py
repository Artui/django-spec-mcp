"""A JSON-RPC client for the kernel mounts, choosing the caller per request."""

from __future__ import annotations

import json
from typing import Any

from django.test import Client

PROTOCOL_VERSION = "2025-11-25"


class KernelClient:
    """Posts JSON-RPC to ``/mcp/`` as the caller ``HeaderAuthBackend`` reads.

    ``user`` and ``scopes`` become the ``X-Test-User`` / ``X-Test-Scopes``
    headers; ``initialize`` opens the session every later call carries.
    """

    def __init__(self, client: Client, *, user: str | None = None, scopes: str = "") -> None:
        self.client = client
        self.user = user
        self.scopes = scopes
        self.session_id: str | None = None

    def post(self, method: str, params: dict[str, Any] | None = None) -> Any:
        headers: dict[str, str] = {"HTTP_X_TEST_SCOPES": self.scopes}
        if self.user is not None:
            headers["HTTP_X_TEST_USER"] = self.user
        if self.session_id is not None:
            headers["HTTP_MCP_SESSION_ID"] = self.session_id
            headers["HTTP_MCP_PROTOCOL_VERSION"] = PROTOCOL_VERSION
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            body["params"] = params
        return self.client.post(
            "/mcp/", data=json.dumps(body), content_type="application/json", **headers
        )

    def initialize(self) -> dict[str, Any]:
        response = self.post(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0.0"},
            },
        )
        assert response.status_code == 200, response.content
        self.session_id = response["Mcp-Session-Id"]
        return response.json()

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """The ``tools/call`` response body."""
        return self.post("tools/call", {"name": name, "arguments": arguments or {}}).json()
