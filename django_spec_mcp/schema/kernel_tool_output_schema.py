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
    schema: dict[str, Any] | None = spec_output_schema(
        binding.spec,
        paginate=binding.paginate,
        projection=binding.audience_projection,
        handle_description=handle_description,
    )
    if schema is None or schema.get("type") != _OBJECT_OR_NULL:
        return schema
    return _admitting_nothing(schema)


# The root type the kernel gives a spec that can present nothing
# (``can_present_nothing``): its item, or ``None``.
_OBJECT_OR_NULL: list[str] = ["object", "null"]


def _admitting_nothing(schema: dict[str, Any]) -> dict[str, Any]:
    """A nothing-presenting spec's schema, as an ``outputSchema`` can state it.

    MCP requires an ``outputSchema`` of type ``"object"``, and a ``None`` is
    served as the empty ``structuredContent`` ``{}``, which the kernel's
    ``["object", "null"]`` with every field required does not admit. So the
    root is ``"object"``, and its ``required`` becomes the drf-services
    shape (``build_output_schema``'s ``may_be_empty``): either every field or
    none at all (``test_a_result_that_presents_nothing_conforms_to_its_schema``).
    A schema with nothing required admits ``{}`` already and keeps no ``anyOf``.
    """
    admitting: dict[str, Any] = {k: v for k, v in schema.items() if k != "required"}
    admitting["type"] = "object"
    if schema.get("required"):
        admitting["anyOf"] = [{"required": schema["required"]}, {"maxProperties": 0}]
    return admitting


__all__ = ["kernel_tool_output_schema"]
