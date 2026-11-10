from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.http import HttpRequest

from django_spec_mcp.auth.types.protected_resource_metadata import ProtectedResourceMetadata
from django_spec_mcp.auth.types.token_info import TokenInfo


@runtime_checkable
class MCPAuthBackend(Protocol):
    """Pluggable authentication for the MCP transport."""

    def authenticate(self, request: HttpRequest) -> TokenInfo | None: ...

    def protected_resource_metadata(self) -> ProtectedResourceMetadata: ...

    def www_authenticate_challenge(
        self, *, scopes: list[str] | None = None, error: str | None = None
    ) -> str: ...


__all__ = ["MCPAuthBackend"]
