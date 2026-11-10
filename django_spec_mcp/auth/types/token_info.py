from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TokenInfo:
    """Authenticated principal carried alongside an MCP request."""

    user: Any
    scopes: tuple[str, ...] = field(default_factory=tuple)
    audience: str | Sequence[str] | None = None
    raw: Any = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def has_all_scopes(self, scopes: list[str] | tuple[str, ...]) -> bool:
        return all(s in self.scopes for s in scopes)


__all__ = ["TokenInfo"]
