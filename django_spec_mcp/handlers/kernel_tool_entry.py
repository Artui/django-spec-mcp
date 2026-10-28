from __future__ import annotations

from typing import Any

from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import render_convention, resolve_bound
from django_spec_mcp.output.resolve_structured_output import resolve_structured_output
from django_spec_mcp.protocol.types.tool import Tool
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.schema.agent_conventions import append_agent_conventions
from django_spec_mcp.schema.kernel_tool_input_schema import kernel_tool_input_schema
from django_spec_mcp.schema.kernel_tool_output_schema import kernel_tool_output_schema


def kernel_tool_entry(binding: KernelToolBinding, context: MCPCallContext) -> dict[str, Any]:
    """A kernel tool's ``tools/list`` entry.

    The schemas are the kernel's (``kernel_tool_input_schema``,
    ``kernel_tool_output_schema``); everything else is assembled from the
    server's settings: the ``outputSchema``
    behind ``include_output_schema``, the handle convention appended to the
    description, and ``annotations`` / ``_meta`` as registered.
    """
    emit_output_schema, _emit_structured_content = resolve_structured_output(
        include_output_schema_override=binding.include_output_schema,
        include_structured_content_override=binding.include_structured_content,
        binding_name=binding.name,
        default_output_schema=context.config.include_output_schema,
        default_structured_content=context.config.include_structured_content,
    )
    output_schema = (
        kernel_tool_output_schema(
            binding,
            handle_description=render_convention(context.conventions.handle_field_description),
        )
        if emit_output_schema
        else None
    )
    return Tool(
        name=binding.name,
        description=append_agent_conventions(
            binding.description,
            binding.audience_projection,
            handle_line=render_convention(context.conventions.handle_line),
        ),
        title=binding.title,
        icons=binding.icons,
        input_schema=kernel_tool_input_schema(
            binding,
            max_page_size=resolve_bound(binding.max_page_size, context.config.max_page_size),
            pool_seeds=context.pool_seeds,
            query_param_on_pages=render_convention(context.conventions.query_param_on_pages),
        ),
        output_schema=output_schema,
        annotations=dict(binding.annotations) or None,
        meta=dict(binding.meta) or None,
    ).to_dict()


__all__ = ["kernel_tool_entry"]
