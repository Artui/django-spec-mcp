from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.http import HttpRequest

from django_spec_mcp.auth.types.protected_resource_metadata import ProtectedResourceMetadata
from django_spec_mcp.auth.types.token_info import TokenInfo


@runtime_checkable
class MCPAuthBackend(Protocol):
    """Pluggable authentication for the MCP transport.

    The transport calls ``authenticate`` on every request; returning
    ``None`` signals "no valid credentials" and the transport emits a 401 whose
    ``WWW-Authenticate`` comes from ``www_authenticate_challenge``.
    ``protected_resource_metadata`` powers the RFC 9728 view, under the mount
    and, through ``MCPServer.well_known_urls``, at the well-known location its
    ``resource`` defines; the view calls ``.to_dict()`` on the returned
    dataclass for the wire shape.

    The backend is the resource server's half of OAuth: the authorization
    server that issues the tokens publishes its own metadata, so nothing here
    describes it.

    The project constructs its backend, with whatever the backend needs, and
    passes the instance to the server as ``auth_backend=``; the package never
    constructs one, so a backend's ``__init__`` may require any arguments.
    """

    def authenticate(self, request: HttpRequest) -> TokenInfo | None: ...

    def protected_resource_metadata(self) -> ProtectedResourceMetadata: ...

    def www_authenticate_challenge(
        self, *, scopes: list[str] | None = None, error: str | None = None
    ) -> str: ...


__all__ = ["MCPAuthBackend"]
