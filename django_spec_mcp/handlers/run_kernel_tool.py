from __future__ import annotations

from typing import Any

from django_service_specs import DispatchError, ServiceError, dispatch

from django_spec_mcp.elicitation.types.resolved_input import ResolvedInput
from django_spec_mcp.handlers.answer_kernel_refusal import answer_kernel_refusal
from django_spec_mcp.handlers.answer_mapped_exception import answer_mapped_exception
from django_spec_mcp.handlers.exception_handler_for import exception_handler_for
from django_spec_mcp.handlers.kernel_utils import (
    KernelAdmission,
    bound_unpaginated_rows,
    not_found_result,
    on_the_wire,
    page_request,
    tool_refusal_scene,
    tool_result,
)
from django_spec_mcp.handlers.present_kernel_result import present_kernel_result
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.tool_result import ToolResult
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding


def run_kernel_tool(
    binding: KernelToolBinding,
    params: dict[str, Any],
    arguments: dict[str, Any],
    admission: KernelAdmission,
    prior: ResolvedInput,
    context: MCPCallContext,
    otel_span: Any,
) -> dict[str, Any] | JsonRpcError:
    """Dispatch an admitted kernel tool call through ``dispatch`` and render its result.

    Reached from ``handle_tools_call`` once the shared shell has applied the
    server arguments, judged the scope gate, merged a retry's answers, asked
    the spec's checks (``admit_kernel_call``) and charged the rate limits, so
    this is only the call: the kernel's ``dispatch`` under ``admission``, the
    principal, grant and call context the checks were asked with, and the
    values of the seeds they name (``test_a_wire_tool_call_resolves_a_checks_seed_once``),
    so a call whose arguments passed the shape check is not asked again and
    resolves those seeds no second time (a call whose
    arguments failed it is counted in ``admit_kernel_call``), a refusal
    answered through ``KERNEL_REFUSALS``, a ``not_found`` worded by
    ``not_found_result``, and the presented value as the tool result. An exception the
    server's ``exception_map`` holds answers as its handler says; any other that is no
    kernel refusal is raised as it was.
    """
    dispatched, page = page_request(binding, arguments, context.config)
    try:
        result = dispatch(
            binding.spec,
            principal=admission.principal,
            grant=admission.grant,
            seeds=admission.seeds,
            arguments=dispatched,
            pool_seeds=context.pool_seeds,
            unknown_arguments=binding.unknown_arguments,
            progress=context.progress,
            call_context=admission.call_context,
        )
    except Exception as exc:
        # One arm, because the map is asked first and declines by returning ``None``:
        # as a leading clause of its own, its ``raise`` would leave the ``try``
        # rather than reach the refusal arm below it.
        handler = exception_handler_for(exc, context.exception_map)
        if handler is not None:
            return answer_mapped_exception(
                handler, exc, config=context.config, span=otel_span
            ).to_dict()
        if not isinstance(exc, DispatchError | ServiceError):
            raise
        # Built here, not ahead of the call: it reads the spec's declaration, and a
        # declaration that raises is the call's to answer, on the same route as ``call_tool``
        # (``test_a_validator_s_declaration_raising_a_mapped_exception_answers_as_its_handler_says``).
        scene = tool_refusal_scene(
            binding, arguments, prior, context, otel_span, admission.call_context
        )
        return on_the_wire(answer_kernel_refusal(exc, scene))
    if result.kind == "not_found":
        return not_found_result(binding).to_dict()
    bounded = bound_unpaginated_rows(
        binding,
        result,
        page,
        context.config,
        exception_map=context.exception_map,
        span=otel_span,
    )
    if isinstance(bounded, ToolResult):
        return bounded.to_dict()
    payload: Any = present_kernel_result(
        binding,
        bounded,
        page,
        arguments=arguments,
        config=context.config,
        conventions=context.conventions,
        exception_map=context.exception_map,
        span=otel_span,
    )
    if isinstance(payload, ToolResult):
        return payload.to_dict()
    return tool_result(binding, payload, config=context.config).to_dict()


__all__ = ["run_kernel_tool"]
