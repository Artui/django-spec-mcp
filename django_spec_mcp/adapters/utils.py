from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def merge_tool_annotations(
    explicit: dict[str, Any] | None, *, read_only: bool, idempotent: bool | None = None
) -> dict[str, Any]:
    """Auto-derive a tool's MCP ``ToolAnnotations``, explicit hints winning."""
    derived: dict[str, Any] = (
        {"readOnlyHint": True} if read_only else {"readOnlyHint": False, "destructiveHint": True}
    )
    if not read_only and idempotent is not None:
        derived["idempotentHint"] = idempotent
    return {**derived, **(explicit or {})}


def merge_meta(*pieces: Mapping[str, Any] | None) -> dict[str, Any]:
    """Shallow-merge ``_meta`` contributions into one bundle, later wins."""
    merged: dict[str, Any] = {}
    for piece in pieces:
        if piece:
            merged.update(piece)
    return merged


__all__ = [
    "merge_meta",
    "merge_tool_annotations",
]
