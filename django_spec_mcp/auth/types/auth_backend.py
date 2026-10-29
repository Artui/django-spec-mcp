from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.http import HttpRequest

from django_spec_mcp.auth.types.authorization_server_metadata import (
    AuthorizationServerMetadata,
)
from django_spec_mcp.auth.types.protected_resource_metadata import ProtectedResourceMetadata
from django_spec_mcp.auth.types.token_info import TokenInfo


@runtime_checkable
class MCPAuthBackend(Protocol):
    """Pluggable authentication for the MCP transport.

    The transport calls ``authenticate`` on every request; returning
    ``None`` signals "no valid credentials" and the transport emits a 401 whose
    ``WWW-Authenticate`` comes from ``www_authenticate_challenge``.
    ``protected_resource_metadata`` powers the
    ``/.well-known/oauth-protected-resource`` view (RFC 9728); the PRM view
    calls ``.to_dict()`` on the returned dataclass for the wire shape.

    ``authorization_server_metadata`` describes the authorization server a
    backend fronts. A backend that hosts none raises ``NotImplementedError``.

    Backends MUST be safe to instantiate without arguments — settings-driven
    configuration belongs inside the backend's own module.
    """

    def authenticate(self, request: HttpRequest) -> TokenInfo | None: ...

    def protected_resource_metadata(self) -> ProtectedResourceMetadata: ...

    def authorization_server_metadata(self) -> AuthorizationServerMetadata: ...

    def www_authenticate_challenge(
        self, *, scopes: list[str] | None = None, error: str | None = None
    ) -> str: ...


__all__ = ["MCPAuthBackend"]
