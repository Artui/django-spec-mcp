from __future__ import annotations

from collections.abc import Sequence


def audience_matches(token_audience: str | Sequence[str] | None, expected: str | None) -> bool:
    """Return True if the token's audience satisfies the configured resource URL."""
    if expected is None:
        return True
    if token_audience is None:
        return False
    if isinstance(token_audience, str):
        return token_audience == expected
    return any(member == expected for member in token_audience)


__all__ = ["audience_matches"]
