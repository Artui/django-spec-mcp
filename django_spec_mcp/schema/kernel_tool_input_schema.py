from __future__ import annotations

from typing import Any

from django_service_specs import spec_input_schema

from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding


def kernel_tool_input_schema(
    binding: KernelToolBinding, *, max_page_size: int | None
) -> dict[str, Any]:
    """The ``inputSchema`` a kernel tool advertises.

    The kernel's ``spec_input_schema`` under the binding's ``unknown_arguments``,
    so the schema is closed exactly when the call refuses an undeclared key, and
    a service tool's parameters sit at the top level as the call takes them.
    Two things the kernel cannot know are this binding's:

    - a ``server_arguments`` name is left out of ``properties`` and
      ``required``, since the server answers for it and drops a caller's value;
    - a paginated ``LIST`` tool advertises ``page`` and ``limit``, as the
      drf-services selector schema does, ``limit`` bounded by ``max_page_size``
      when there is one.
    """
    schema: dict[str, Any] = dict(
        spec_input_schema(binding.spec, unknown_arguments=binding.unknown_arguments)
    )
    properties: dict[str, Any] = dict(schema.get("properties") or {})
    answered: frozenset[str] = binding.server_arguments.names()
    for name in answered:
        properties.pop(name, None)
    if binding.paginate:
        properties["page"] = {"type": "integer", "minimum": 1}
        limit: dict[str, Any] = {"type": "integer", "minimum": 1}
        if max_page_size is not None:
            limit["maximum"] = max_page_size
        properties["limit"] = limit
    schema["properties"] = properties
    if "required" in schema:
        schema["required"] = [name for name in schema["required"] if name not in answered]
    return schema


__all__ = ["kernel_tool_input_schema"]
