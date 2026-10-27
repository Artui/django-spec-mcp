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
    its place (``_admitting_nothing``, ``_a_list``). ``None`` for an image or audio tool, whose
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
    if schema is None:
        return None
    if schema.get("type") == _OBJECT_OR_NULL:
        return _admitting_nothing(schema)
    if schema.get("type") == _ARRAY_OR_NULL:
        return _a_list(schema)
    return schema


# The root types the kernel gives a spec that can present nothing
# (``can_present_nothing``): its item or ``None``, and a ``many`` or
# ``returns_many`` service's rows or ``None``.
_OBJECT_OR_NULL: list[str] = ["object", "null"]
_ARRAY_OR_NULL: list[str] = ["array", "null"]


def _a_list(schema: dict[str, Any]) -> dict[str, Any]:
    """A list-shaped spec's schema when the list may present nothing.

    No MCP result carries a ``null`` as its structured content, so the
    ``"null"`` the kernel adds describes nothing a client is served. A list
    that presents nothing is the empty list, which ``"array"`` already admits,
    and each row stays as strict as declared
    (``test_a_list_that_may_present_nothing_is_advertised_as_a_list``). The
    result builder serving that empty list rather than ``{}`` is outside this
    schema, and ``test_a_list_that_presents_nothing_conforms_to_its_schema``
    fails as expected until it does.
    """
    return {**schema, "type": "array"}


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
