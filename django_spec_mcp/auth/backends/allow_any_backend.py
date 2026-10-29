from __future__ import annotations

from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest

from django_spec_mcp.auth.bearer_challenge import bearer_challenge
from django_spec_mcp.auth.types.protected_resource_metadata import ProtectedResourceMetadata
from django_spec_mcp.auth.types.token_info import TokenInfo


class AllowAnyBackend:
    """Development / test backend that authenticates every request as anonymous.

    DO NOT use in production. The ``protected_resource_metadata`` payload is
    intentionally minimal so misconfiguration is loud rather than silent.
    """

    def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        user: Any = getattr(request, "user", None) or AnonymousUser()
        return TokenInfo(user=user, scopes=(), audience=None, raw=None)

    def protected_resource_metadata(self) -> ProtectedResourceMetadata:
        return ProtectedResourceMetadata(
            resource="(unset)",
            authorization_servers=[],
            bearer_methods_supported=["header"],
            scopes_supported=[],
            warning="AllowAnyBackend is for development only.",
        )

    def www_authenticate_challenge(
        self, *, scopes: list[str] | None = None, error: str | None = None
    ) -> str:
        return bearer_challenge(resource_metadata_url=None, scopes=scopes, error=error)


__all__ = ["AllowAnyBackend"]
