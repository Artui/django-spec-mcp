from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django_spec_mcp.protocol.types.tool_content_block import ToolContentBlock


@dataclass(frozen=True)
class PromptMessage:
    """One conversation turn returned by ``prompts/get``."""

    role: str
    content: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}

    @classmethod
    def text(cls, role: str, text: str) -> PromptMessage:
        """Convenience constructor for the common case of a text turn."""
        return cls(role=role, content={"type": "text", "text": text})

    @classmethod
    def block(cls, role: str, block: ToolContentBlock) -> PromptMessage:
        """Build a turn from any content block."""
        return cls(role=role, content=block.to_dict())


__all__ = ["PromptMessage"]
