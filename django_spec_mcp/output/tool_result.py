from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django_service_specs import UNSET

from django_spec_mcp.output.build_content_blocks import build_content_blocks
from django_spec_mcp.output.encode_json import encode_json
from django_spec_mcp.output.error_tool_result import build_error_tool_result
from django_spec_mcp.protocol.types.tool_content_block import ToolContentBlock
from django_spec_mcp.protocol.types.tool_result import ToolResult
from django_spec_mcp.types.tool_content_kind import ToolContentKind


def build_tool_result(
    payload: Any,
    *,
    is_error: bool = False,
    include_structured_content: bool = True,
    meta: dict[str, Any] | None = None,
    content_kind: ToolContentKind = ToolContentKind.TEXT,
    content_mime_type: str | None = None,
    binding_name: str | None = None,
) -> ToolResult:
    """Build a tool result for a successful (or tool-level error) call."""
    if content_kind is not ToolContentKind.TEXT:
        blocks = build_content_blocks(
            payload, content_kind=content_kind, mime_type=content_mime_type
        )
        if isinstance(blocks, str):
            label = f"Tool {binding_name!r}" if binding_name else "This tool"
            return build_error_tool_result(f"{label} {blocks}", error_type="output_encoding")
        return ToolResult(
            content=blocks,
            structured_content=_as_object(payload)
            if include_structured_content and content_kind is ToolContentKind.RESOURCE_LINK
            else UNSET,
            is_error=is_error,
            meta=meta,
        )

    payload = _as_object(payload)
    return ToolResult(
        content=[ToolContentBlock.text_block(encode_json(payload))],
        structured_content=payload
        if include_structured_content and isinstance(payload, Mapping)
        else UNSET,
        is_error=is_error,
        meta=meta,
    )


def _as_object(payload: Any) -> Any:
    """``payload`` as the object MCP requires structured content to be."""
    if payload is None:
        return {}
    if isinstance(payload, list | tuple):
        return {"items": payload}
    return payload


__all__ = ["build_tool_result"]
