from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured

from django_spec_mcp.auth.types.token_info import TokenInfo


def principal_for_token(token: TokenInfo) -> str:
    """Derive the stable principal id that owns a session or a task."""
    user: Any = token.user
    pk: Any = getattr(user, "pk", None)
    if pk is not None:
        return f"user:{pk}"
    if user is None or getattr(user, "is_authenticated", None) is False:
        return "anonymous"
    raise ImproperlyConfigured(
        f"The auth backend resolved an authenticated caller to {type(user).__name__}, "
        "which has no 'pk'. Sessions and tasks are owned by a principal id derived "
        "from that primary key, so every such caller would share one principal — "
        "each able to present the others' session ids and read their task results. "
        "Give the resolved user a 'pk', or return AnonymousUser if the caller is "
        "genuinely unauthenticated."
    )


__all__ = ["principal_for_token"]
