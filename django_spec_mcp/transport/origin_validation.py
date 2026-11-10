from __future__ import annotations

from collections.abc import Sequence


def is_origin_allowed(origin: str | None, allowed_origins: Sequence[str]) -> bool:
    """Return ``True`` if the request's ``Origin`` header matches the allowlist."""
    if not origin:
        return True
    if "*" in allowed_origins:
        return True
    return origin in allowed_origins


__all__ = ["is_origin_allowed"]
