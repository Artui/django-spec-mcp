from __future__ import annotations

from typing import Any

from django_spec_mcp.registry.types.chain_tool_binding import ChainToolBinding
from django_spec_mcp.schema.input_schema import build_input_schema


def build_chain_tool_input_schema(binding: ChainToolBinding) -> dict[str, Any]:
    """Build the JSON Schema for a chain tool's ``inputSchema``.

    The chain advertises a single input shape: its explicit
    ``input_serializer`` when set, otherwise the first step's serializer. A
    binding with neither yields an empty ``{"type": "object"}``.
    """
    return build_input_schema(binding.resolved_input_serializer)


__all__ = ["build_chain_tool_input_schema"]
