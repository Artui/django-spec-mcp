"""``OutputFormat`` - encoding of a ToolResult's human-readable text block."""

from __future__ import annotations

from enum import Enum


class OutputFormat(str, Enum):
    """Encoding of a ``ToolResult``'s human-readable text block.

    Only ``content[0]`` varies; ``structuredContent`` is always JSON.

    Attributes:
        JSON: Pretty-printed JSON. The safe default.
        TOON: Token-oriented object notation, compact for large uniform arrays.
            Falls back to JSON when the optional ``toon`` extra is absent.
        AUTO: Per-payload choice — TOON for a uniform list of objects, JSON
            otherwise.
    """

    JSON = "json"
    TOON = "toon"
    AUTO = "auto"

    @classmethod
    def coerce(cls, value: OutputFormat | str | None) -> OutputFormat:
        """Accept either an enum member or its string value; default to JSON."""
        if value is None:
            return cls.JSON
        if isinstance(value, cls):
            return value
        return cls(value)
