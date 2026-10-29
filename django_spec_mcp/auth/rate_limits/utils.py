from __future__ import annotations

from typing import Any

from django.http import HttpRequest

from django_spec_mcp.auth.types.token_info import TokenInfo


def default_key(request: HttpRequest, token: TokenInfo) -> str:
    """The bucket dimension every limiter defaults to: the user, else the remote address."""
    user_id: Any = getattr(token.user, "id", None)
    if user_id is not None:
        return f"u:{user_id}"
    return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


def require_positive(name: str, value: float) -> None:
    """Refuse a limiter bound that is zero or negative, naming the argument."""
    if value <= 0:
        raise ValueError(f"{name} must be positive")
