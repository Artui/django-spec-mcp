from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.http import HttpRequest

from django_spec_mcp.auth.types.token_info import TokenInfo


@runtime_checkable
class MCPRateLimit(Protocol):
    """Per-binding rate limiter, evaluated after authentication and permissions."""

    def consume(self, request: HttpRequest, token: TokenInfo) -> int | None: ...


__all__ = ["MCPRateLimit"]
