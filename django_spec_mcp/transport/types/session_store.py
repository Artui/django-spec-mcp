from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SessionStore(Protocol):
    """Pluggable persistence for ``MCP-Session-Id`` lifecycle."""

    def create(self, *, principal_id: str) -> str: ...

    def exists(self, session_id: str) -> bool: ...

    def owner(self, session_id: str) -> str | None: ...

    def destroy(self, session_id: str) -> None: ...


__all__ = ["SessionStore"]
