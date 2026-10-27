"""An auth backend for tests, resolving the caller from two request headers."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest

from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo

USER_HEADER = "X-Test-User"
"""The username of the caller; absent, the caller is anonymous."""

SCOPES_HEADER = "X-Test-Scopes"
"""The caller's token scopes, space-separated; absent, it has none."""


class HeaderAuthBackend(AllowAnyBackend):
    """Resolve the principal and its scopes from ``X-Test-User`` / ``X-Test-Scopes``.

    A mount passes it explicitly, so a test chooses the caller per request:
    the named user, looked up by username (inactive users included, so a
    deactivated principal can be sent), or ``AnonymousUser`` with no header.
    An unknown username is unauthenticated, answered by the transport's 401.
    """

    def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        username: str | None = request.headers.get(USER_HEADER)
        scopes: tuple[str, ...] = tuple(request.headers.get(SCOPES_HEADER, "").split())
        if username is None:
            return TokenInfo(user=AnonymousUser(), scopes=scopes, audience=None, raw=None)
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            return None
        return TokenInfo(user=user, scopes=scopes, audience=None, raw=None)
