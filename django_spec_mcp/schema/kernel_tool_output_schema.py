from __future__ import annotations

from typing import Any

from django_service_specs import spec_output_schema

from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.types.tool_content_kind import ToolContentKind


def kernel_tool_output_schema(
    binding: KernelToolBinding, *, handle_description: str | None
) -> dict[str, Any] | None:
    """The ``outputSchema`` a kernel tool can advertise, before the binding's
    ``include_output_schema`` decides whether it does.

    The kernel's ``spec_output_schema``, enveloped when the tool paginates and
    projected through the binding's audience markings, so it names the fields
    the presented payload carries. ``None`` for an image or audio tool, whose
    result is a media block with no structured content to describe, and for a
    spec the kernel has no schema for.
    """
    if binding.content_kind in (ToolContentKind.IMAGE, ToolContentKind.AUDIO):
        return None
    return spec_output_schema(
        binding.spec,
        paginate=binding.paginate,
        projection=binding.audience_projection,
        handle_description=handle_description,
    )


__all__ = ["kernel_tool_output_schema"]
