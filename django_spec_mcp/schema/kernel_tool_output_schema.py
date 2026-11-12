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
    the presented payload carries, and with the ``"null"`` the kernel admits for
    a spec that can present nothing restated as what this transport serves in
    its place (``_admitting_nothing``, ``_a_list``). A list the kernel describes
    as the root ``"array"`` is held under ``items`` (``_items_of``), because MCP
    declares every ``outputSchema`` an object. ``None`` for an image or audio
    tool, whose result is a media block with no structured content to describe,
    and for a spec the kernel has no schema for.
    """
    if binding.content_kind in (ToolContentKind.IMAGE, ToolContentKind.AUDIO):
        return None
    schema: dict[str, Any] | None = spec_output_schema(
        binding.spec,
        paginate=binding.paginate,
        projection=binding.audience_projection,
        handle_description=handle_description,
    )
    if schema is None:
        return None
    if schema.get("type") == _OBJECT_OR_NULL:
        return _admitting_nothing(schema)
    if schema.get("type") == _ARRAY_OR_NULL:
        return _items_of(_a_list(schema))
    if schema.get("type") == "array":
        return _items_of(schema)
    return schema


# The root types the kernel gives a spec that can present nothing
# (``can_present_nothing``): its item or ``None``, and a ``many`` or
# ``returns_many`` service's rows or ``None``.
_OBJECT_OR_NULL: list[str] = ["object", "null"]
_ARRAY_OR_NULL: list[str] = ["array", "null"]


def _a_list(schema: dict[str, Any]) -> dict[str, Any]:
    """A list-shaped spec's schema when the list may present nothing."""
    return {**schema, "type": "array"}


def _items_of(array: dict[str, Any]) -> dict[str, Any]:
    """An unpaged list's schema, held under ``items`` in an object."""
    return {"type": "object", "properties": {"items": array}, "required": ["items"]}


def _admitting_nothing(schema: dict[str, Any]) -> dict[str, Any]:
    """A nothing-presenting spec's schema, as an ``outputSchema`` can state it."""
    admitting: dict[str, Any] = {k: v for k, v in schema.items() if k != "required"}
    admitting["type"] = "object"
    if schema.get("required"):
        admitting["anyOf"] = [{"required": schema["required"]}, {"maxProperties": 0}]
    return admitting


__all__ = ["kernel_tool_output_schema"]
