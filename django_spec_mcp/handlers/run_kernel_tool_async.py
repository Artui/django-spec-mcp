from __future__ import annotations

from typing import Any

from django_service_specs import DispatchError, ServiceError, adispatch

from django_spec_mcp._compat.acall import acall
from django_spec_mcp.elicitation.types.resolved_input import ResolvedInput
from django_spec_mcp.handlers.answer_kernel_refusal import answer_kernel_refusal
from django_spec_mcp.handlers.kernel_utils import (
    KernelAdmission,
    admitted_arguments,
    bound_unpaginated_rows,
    not_found_result,
    on_the_wire,
    page_request,
    tool_result,
)
from django_spec_mcp.handlers.present_kernel_result import present_kernel_result
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.types.kernel_input_round import KernelInputRound
from django_spec_mcp.handlers.types.kernel_refusal_scene import KernelRefusalScene
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.tool_result import ToolResult
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding


async def run_kernel_tool_async(
    binding: KernelToolBinding,
    params: dict[str, Any],
    arguments: dict[str, Any],
    admission: KernelAdmission,
    prior: ResolvedInput,
    context: MCPCallContext,
    otel_span: Any,
) -> dict[str, Any] | JsonRpcError:
    """Async sibling of ``run_kernel_tool``, through ``adispatch``.

    The presenter runs off the event loop through ``acall``, since presenting a
    lazy list reads the database. Reached from ``handle_tools_call_async`` once
    the shared shell has applied the server arguments, judged the scope gate,
    merged a retry's answers, asked the spec's checks and charged the rate
    limits, so this is only the call: the kernel's ``adispatch`` under
    ``admission``, as in the sync sibling, a refusal answered through
    ``KERNEL_REFUSALS``, a ``not_found`` worded by ``not_found_result``, and the
    presented value as the tool result.
    """
    dispatched, page = page_request(binding, arguments, context.config)
    scene = KernelRefusalScene(
        arguments=arguments,
        config=context.config,
        conventions=context.conventions,
        # A selector asks for nothing: a request for input from one is a
        # service error, not a round trip
        # (``test_a_kernel_selector_ignores_a_round_trip``).
        input_round=None
        if binding.is_selector
        else KernelInputRound(prior, context, admitted_arguments(binding, context.pool_seeds)),
        span=otel_span,
    )
    try:
        result = await adispatch(
            binding.spec,
            principal=admission.principal,
            grant=admission.grant,
            arguments=dispatched,
            pool_seeds=context.pool_seeds,
            unknown_arguments=binding.unknown_arguments,
            progress=context.progress,
            call_context=admission.call_context,
        )
    except (DispatchError, ServiceError) as exc:
        return on_the_wire(answer_kernel_refusal(exc, scene))
    if result.kind == "not_found":
        return not_found_result(binding).to_dict()
    # Off the loop, as the presenter is: the bound's slice runs the query.
    bounded = await acall(bound_unpaginated_rows, binding, result, page, context.config)
    if isinstance(bounded, ToolResult):
        return bounded.to_dict()
    payload: Any = await acall(
        present_kernel_result,
        binding,
        bounded,
        page,
        arguments=arguments,
        config=context.config,
        conventions=context.conventions,
    )
    if isinstance(payload, ToolResult):
        return payload.to_dict()
    return tool_result(binding, payload, config=context.config).to_dict()


__all__ = ["run_kernel_tool_async"]
