from __future__ import annotations

from django_spec_mcp.types.tool_content_kind import ToolContentKind

# The kinds a file takes on a tool, and the only ones that carry no structuredContent.
FILE_CONTENT_KINDS: frozenset[ToolContentKind] = frozenset(
    {ToolContentKind.IMAGE, ToolContentKind.AUDIO, ToolContentKind.RESOURCE}
)

__all__ = ["FILE_CONTENT_KINDS"]
