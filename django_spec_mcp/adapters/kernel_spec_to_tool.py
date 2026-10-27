from __future__ import annotations

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
from django_spec_mcp.protocol.types.icon import Icon
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.types.output_format import OutputFormat
from django_spec_mcp.types.task_policy import TaskPolicy
from django_spec_mcp.types.tool_content_kind import ToolContentKind


def kernel_spec_to_tool(
    spec: Any,
    *,
    name: str,
    description: str | None,
    title: str | None,
    icons: tuple[Icon, ...],
    annotations: dict[str, Any] | None,
    meta: dict[str, Any],
    output_format: OutputFormat,
    content_kind: ToolContentKind,
    content_mime_type: str | None,
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
    scopes: tuple[str, ...] | None,
    unknown_arguments: UnknownArguments,
    server_arguments: ServerArguments | None,
) -> KernelToolBinding:
    """Build the binding ``MCPServer.register_tool`` registers for a kernel spec.

    Refuses, before anything is built, what the binding cannot mean:

    - ``spec`` that is not a kernel ``ServiceSpec`` or ``SelectorSpec``, a
      ``TypeError``. A drf-services spec is not one, and goes to
      ``register_service_tool`` / ``register_selector_tool``.
    - a knob of the other kind of tool: ``paginate`` / ``max_page_size`` on a
      service tool or a ``RETRIEVE``, ``invalidates`` on a selector tool.
    - a ``task_policy`` other than ``FORBIDDEN``, which the task path does not
      run for a kernel spec yet.
    - ``scopes=()``, which reads as a gate and admits everyone.
    - ``UnknownArguments.PASSTHROUGH``, which the kernel never honours: an
      argument a spec does not declare reaches no callable.

    Then compiles the spec's two schemas, so a declaration the kernel cannot
    describe fails here rather than taking every later ``tools/list`` down
    with it.

    The MCP hints are derived as for the drf-services tools
    (``merge_tool_annotations``): a selector tool is read-only, a service tool
    destructive with its declared ``idempotent`` as ``idempotentHint``, and a
    hint passed in ``annotations`` wins.
    """
    if not isinstance(spec, ServiceSpec | SelectorSpec):
        raise TypeError(
            f"Tool {name!r}: register_tool takes a django_service_specs ServiceSpec or "
            f"SelectorSpec, not {type(spec).__module__}.{type(spec).__qualname__}. A "
            "drf-services spec registers through register_service_tool or "
            "register_selector_tool."
        )
    _refuse_other_kinds_knobs(name, spec, invalidates, paginate, max_page_size)
    if task_policy is not TaskPolicy.FORBIDDEN:
        raise ImproperlyConfigured(
            f"Tool {name!r}: task_policy={task_policy.name} is not yet supported for a "
            "kernel spec; such a tool runs inline. Leave task_policy at its default."
        )
    # ``test_an_empty_scope_tuple_is_refused`` holds ``not scopes``, and
    # ``test_scopes_become_the_scope_gate_and_none_is_no_gate`` holds the ``None`` test.
    if scopes is not None and not scopes:
        raise ImproperlyConfigured(
            f"Tool {name!r}: scopes=() permits every caller while reading as a gate. "
            "Name at least one scope, or leave scopes=None for no scope gate."
        )
    if unknown_arguments is UnknownArguments.PASSTHROUGH:
        raise ImproperlyConfigured(
            f"Tool {name!r}: UnknownArguments.PASSTHROUGH is not honoured by the kernel, "
            "where an argument the spec does not declare never reaches a callable. Pass "
            "UnknownArguments.IGNORE or UnknownArguments.REJECT."
        )
    # Compiled here for what they raise: a declaration the kernel cannot describe
    # fails the server's startup, not the first listing.
    spec_input_schema(spec, unknown_arguments=unknown_arguments)
    spec_output_schema(spec, paginate=paginate)
    return KernelToolBinding(
        name=name,
        description=description,
        spec=spec,
        title=title,
        icons=icons,
        annotations=merge_tool_annotations(
            annotations,
            read_only=isinstance(spec, SelectorSpec),
            idempotent=spec.idempotent if isinstance(spec, ServiceSpec) else None,
        ),
        meta=meta,
        output_format=output_format,
        content_kind=content_kind,
        content_mime_type=content_mime_type,
        include_structured_content=include_structured_content,
        include_output_schema=include_output_schema,
        invalidates=invalidates,
        paginate=paginate,
        max_page_size=max_page_size,
        max_result_bytes=max_result_bytes,
        dispatch_timeout=dispatch_timeout,
        always_listed=always_listed,
        rate_limits=rate_limits,
        scopes=None if scopes is None else tuple(scopes),
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
    """Refuse a knob that belongs to the other kind of tool.

    ``paginate`` and ``max_page_size`` are each refused on their own, so a
    service tool given only ``max_page_size`` is refused as one given only
    ``paginate`` is (``test_a_service_tool_refuses_each_selector_knob``).
    """
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
    # ``test_a_list_tool_takes_the_page_knobs`` holds the kind test, and every
    # RETRIEVE tool registered without page knobs (the testapp's ``invoices.get``,
    # through ``test_initialize_lists_and_calls_through_the_kernel``) holds ``pages``.
    if pages and spec.kind is not SelectorKind.LIST:
        raise ImproperlyConfigured(
            f"Tool {name!r}: paginate and max_page_size belong to a LIST selector "
            "tool; a RETRIEVE answers one row."
        )


__all__ = ["kernel_spec_to_tool"]
