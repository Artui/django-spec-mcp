from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django_service_specs import UNSET

from django_spec_mcp.output.encode_json import encode_json
from django_spec_mcp.protocol.types.tool_content_block import ToolContentBlock
from django_spec_mcp.protocol.types.tool_result import ToolResult


def build_error_tool_result(
    message: str,
    *,
    error_type: str,
    detail: Mapping[str, Any] | None = None,
) -> ToolResult:
    """Build an ``isError: true`` tool result for a tool-level failure."""
    error: dict[str, Any] = {"type": error_type, "message": message}
    if detail:
        error.update(detail)
    payload: dict[str, Any] = {"error": error}
    return ToolResult(
        content=[ToolContentBlock.text_block(encode_json(payload))],
        structured_content=UNSET,
        is_error=True,
    )


__all__ = ["build_error_tool_result"]
