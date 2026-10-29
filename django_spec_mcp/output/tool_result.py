from __future__ import annotations

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
    """Build a [`ToolResult`][django_spec_mcp.protocol.types.tool_result.ToolResult]
    for a successful (or tool-level error) call.

    Args:
        payload: The JSON-shaped tool output. Becomes ``structuredContent``
            verbatim and is also rendered, as JSON, into the first content block.
        is_error: Stamped onto the result as ``isError``.
        include_structured_content: ``False`` omits ``structuredContent``
            entirely. The text block still carries the full payload, so a
            client that doesn't consume the structured field loses nothing.
        meta: The base protocol's ``_meta`` bundle on the *result envelope* —
            per-call, unlike the static ``_meta`` already advertised on the
            ``tools/list`` entry. Omitted from the payload when empty.
        content_kind: The block type the binding declared. Anything other than
            ``TEXT`` is a media or resource-link block rather than JSON text, and
            a payload that doesn't match the declared kind comes back as an
            ``isError`` result naming the binding.
        content_mime_type: Media type for a non-``TEXT`` block.
        binding_name: Names the binding in that mismatch message.

    A ``None`` payload is a result with **nothing to present**: an
    ``allow_none`` RETRIEVE that found no row, a service whose output re-read
    selector found none, or a service with no re-read declaring
    ``ServiceSpec(allow_none=True)`` that returned ``None``. It is served as
    ``{}``, both as ``structuredContent`` and as the text block, because MCP
    requires structured content to be an object and asks the text block to
    carry the same JSON. This is the one place the rule lives, so a selector
    and a service answer it the same way on every entry point. The
    advertised ``outputSchema`` of those tools admits the ``{}``
    (``build_output_schema(may_be_empty=...)``). A service with no re-read that
    returns ``None`` without declaring ``allow_none`` is served ``{}`` too,
    against a schema that still requires its fields, because the kernel
    presents an undeclared ``None`` rather than refusing it
    (``test_an_undeclared_none_is_still_served_empty_against_a_strict_schema``);
    ``allow_none=True``
    is the declaration that makes the schema admit it
    (``test_a_service_declaring_allow_none_serves_an_empty_object_its_schema_admits``).
    """
    if content_kind is not ToolContentKind.TEXT:
        blocks = build_content_blocks(
            payload, content_kind=content_kind, mime_type=content_mime_type
        )
        if isinstance(blocks, str):
            label = f"Tool {binding_name!r}" if binding_name else "This tool"
            return build_error_tool_result(f"{label} {blocks}", error_type="output_encoding")
        return ToolResult(
            content=blocks,
            # Media blocks carry no ``structuredContent`` — binary is not JSON.
            # Resource links do: the links are an ordinary JSON payload.
            structured_content=payload
            if include_structured_content and content_kind is ToolContentKind.RESOURCE_LINK
            else UNSET,
            is_error=is_error,
            meta=meta,
        )

    # Only the text kind: a media or resource-link block has its own payload
    # contract, which ``build_content_blocks`` answers above.
    if payload is None:
        payload = {}
    return ToolResult(
        content=[ToolContentBlock.text_block(encode_json(payload))],
        structured_content=payload if include_structured_content else UNSET,
        is_error=is_error,
        meta=meta,
    )


__all__ = ["build_tool_result"]
