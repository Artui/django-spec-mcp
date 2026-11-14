from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    UNSET,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceSpec,
    UnknownArguments,
    UnsetType,
    spec_input_schema,
    spec_output_schema,
)

from django_spec_mcp.adapters.utils import merge_tool_annotations
from django_spec_mcp.handlers.kernel_utils import call_context
from django_spec_mcp.protocol.types.icon import Icon
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.registry.types.utils import derived_tool_content, scope_names
from django_spec_mcp.types.task_policy import TaskPolicy
from django_spec_mcp.types.tool_content_kind import ToolContentKind


def kernel_spec_to_tool(
    spec: Any,
    *,
    name: str,
    description: str | None,
    title: str | None,
    display_name: str | None,
    display_description: str | None,
    icons: tuple[Icon, ...],
    annotations: dict[str, Any] | None,
    meta: dict[str, Any],
    content_kind: ToolContentKind | UnsetType,
    content_mime_type: str | None | UnsetType,
    include_structured_content: bool | None,
    include_output_schema: bool | None,
    task_policy: TaskPolicy,
    invalidates: tuple[str, ...],
    paginate: bool,
    max_page_size: int | None | UnsetType,
    max_result_bytes: int | None | UnsetType,
    dispatch_timeout: float | None | UnsetType,
    always_listed: bool,
    rate_limits: tuple[Any, ...],
    scopes: str | Sequence[str] | None,
    unknown_arguments: UnknownArguments,
    server_arguments: ServerArguments | None,
) -> KernelToolBinding:
    """Build the binding ``MCPServer.register_tool`` registers for a kernel spec."""
    if not isinstance(spec, ServiceSpec | SelectorSpec):
        raise TypeError(
            f"Tool {name!r}: register_tool takes a django_service_specs ServiceSpec or "
            f"SelectorSpec, not {type(spec).__module__}.{type(spec).__qualname__}."
        )
    _refuse_other_kinds_knobs(name, spec, invalidates, paginate, max_page_size)
    if unknown_arguments is UnknownArguments.PASSTHROUGH:
        raise ImproperlyConfigured(
            f"Tool {name!r}: UnknownArguments.PASSTHROUGH is not honoured by the kernel, "
            "where an argument the spec does not declare never reaches a callable. Pass "
            "UnknownArguments.IGNORE or UnknownArguments.REJECT."
        )
    spec_input_schema(
        spec, unknown_arguments=unknown_arguments, call_context=call_context(None, name)
    )
    spec_output_schema(spec, paginate=paginate)
    content_kind, content_mime_type = derived_tool_content(
        f"Tool {name!r}", spec, content_kind, content_mime_type
    )
    return KernelToolBinding(
        name=name,
        description=description,
        spec=spec,
        title=title,
        display_name=display_name,
        display_description=display_description,
        icons=icons,
        annotations=merge_tool_annotations(
            annotations,
            read_only=isinstance(spec, SelectorSpec),
            idempotent=spec.idempotent if isinstance(spec, ServiceSpec) else None,
        ),
        meta=meta,
        content_kind=content_kind,
        content_mime_type=content_mime_type,
        include_structured_content=include_structured_content,
        include_output_schema=include_output_schema,
        task_policy=task_policy,
        invalidates=invalidates,
        paginate=paginate,
        max_page_size=max_page_size,
        max_result_bytes=max_result_bytes,
        dispatch_timeout=dispatch_timeout,
        always_listed=always_listed,
        rate_limits=rate_limits,
        scopes=scope_names(f"Tool {name!r}", scopes),
        unknown_arguments=unknown_arguments,
        server_arguments=server_arguments or ServerArguments(),
    )


def _refuse_other_kinds_knobs(
    name: str,
    spec: ServiceSpec | SelectorSpec,
    invalidates: tuple[str, ...],
    paginate: bool,
    max_page_size: int | None | UnsetType,
) -> None:
    """Refuse a knob that belongs to the other kind of tool."""
    pages: bool = paginate or max_page_size is not UNSET
    if isinstance(spec, ServiceSpec):
        if pages:
            raise ImproperlyConfigured(
                f"Tool {name!r}: paginate and max_page_size belong to a LIST selector "
                "tool; a service tool's result is never paginated."
            )
        return
    if invalidates:
        raise ImproperlyConfigured(
            f"Tool {name!r}: invalidates belongs to a service tool; a selector tool "
            "reads and changes nothing."
        )
    if pages and spec.kind is not SelectorKind.LIST:
        raise ImproperlyConfigured(
            f"Tool {name!r}: paginate and max_page_size belong to a LIST selector "
            "tool; a RETRIEVE answers one row."
        )


__all__ = ["kernel_spec_to_tool"]
