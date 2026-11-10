from __future__ import annotations

from typing import Any

from django_service_specs import DEFAULT_POOL_SEEDS, PoolSeeds, spec_caller_input_schema

from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.schema.agent_conventions import PAGED_QUERY_PARAM_SCOPE
from django_spec_mcp.schema.utils import end_sentence


def kernel_tool_input_schema(
    binding: KernelToolBinding,
    *,
    max_page_size: int | None,
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
    query_param_on_pages: str | None = PAGED_QUERY_PARAM_SCOPE,
    call_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The ``inputSchema`` a kernel tool advertises."""
    schema = spec_caller_input_schema(
        binding.spec,
        pool_seeds=pool_seeds,
        server_arguments=binding.server_arguments,
        unknown_arguments=binding.unknown_arguments,
        call_context=call_context,
    )
    if binding.paginate:
        properties: dict[str, Any] = schema["properties"]
        if query_param_on_pages is not None:
            for name in binding.spec.shapes.names() & properties.keys():
                properties[name] = _on_each_item(properties[name], query_param_on_pages)
        properties["page"] = {"type": "integer", "minimum": 1}
        limit: dict[str, Any] = {"type": "integer", "minimum": 1}
        if max_page_size is not None:
            limit["maximum"] = max_page_size
        properties["limit"] = limit
    return schema


def _on_each_item(fragment: dict[str, Any], scope: str) -> dict[str, Any]:
    """A shape's property on a paged tool, told it applies to each item."""
    declared: str | None = fragment.get("description")
    return {**fragment, "description": f"{end_sentence(declared)} {scope}" if declared else scope}


__all__ = ["kernel_tool_input_schema"]
