from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django_service_specs import UNSET

from django_spec_mcp.protocol.types.tool_content_block import ToolContentBlock


@dataclass(frozen=True)
class ToolResult:
    """The result of a successful ``tools/call``."""

    content: list[ToolContentBlock] = field(default_factory=list)
    structured_content: Any = UNSET
    is_error: bool = False
    meta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"content": [block.to_dict() for block in self.content]}
        if self.structured_content is not UNSET:
            out["structuredContent"] = self.structured_content
        if self.is_error:
            out["isError"] = True
        if self.meta:
            out["_meta"] = self.meta
        return out


__all__ = ["ToolResult"]
