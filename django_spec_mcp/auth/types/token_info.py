from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TokenInfo:
    """Authenticated principal carried alongside an MCP request.

    Backends construct this once per request and attach it to the Django
    ``HttpRequest`` (as ``request.mcp_token``). A binding's scope gate reads its
    ``scopes``, and a spec's ``PermissionCheck`` reads it off the request in
    ``call_context``.

    Attributes:
        user: The resolved Django user, or an ``AnonymousUser`` equivalent.
            ``Any`` because the user model is project-defined.
        scopes: OAuth scopes proven by the bearer token.
        audience: The resource or resources the token was issued for: a JWT's
            ``aud``, which may be an array, or django-oauth-toolkit's
            ``resource`` list. RFC 8707 requires this server's canonical URL to
            be among them; backends own that comparison, through
            ``audience_matches``.
        raw: Backend-specific opaque payload — the ``AccessToken`` row, the JWT
            claims dict — for advanced use cases.
    """

    user: Any
    scopes: tuple[str, ...] = field(default_factory=tuple)
    audience: str | Sequence[str] | None = None
    raw: Any = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def has_all_scopes(self, scopes: list[str] | tuple[str, ...]) -> bool:
        return all(s in self.scopes for s in scopes)


__all__ = ["TokenInfo"]
