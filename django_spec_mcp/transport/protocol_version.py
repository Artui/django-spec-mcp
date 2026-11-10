from __future__ import annotations

from collections.abc import Sequence


def resolve_protocol_version(header_value: str | None, supported: Sequence[str]) -> str | None:
    """Validate the ``MCP-Protocol-Version`` header against ``supported``."""
    if not header_value:
        return None
    if header_value not in supported:
        return None
    return header_value


__all__ = ["resolve_protocol_version"]
