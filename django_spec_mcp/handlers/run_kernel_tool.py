from __future__ import annotations

from typing import Any

from django_service_specs import DispatchError, ServiceError, dispatch

from django_spec_mcp.elicitation.types.resolved_input import ResolvedInput
from django_spec_mcp.handlers.answer_kernel_refusal import answer_kernel_refusal
from django_spec_mcp.handlers.kernel_utils import (
    call_context,
    carry_token,
    not_found_result,
    on_the_wire,
    page_request,
    present_tool_result,
    tool_result,
)
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.types.kernel_refusal_scene import KernelRefusalScene
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding


def run_kernel_tool(
    binding: KernelToolBinding,
    params: dict[str, Any],
    arguments: dict[str, Any],
    prior: ResolvedInput,
    context: MCPCallContext,
    otel_span: Any,
) -> dict[str, Any] | JsonRpcError:
    """Dispatch an admitted kernel tool call through ``dispatch`` and render its result.

    Reached from ``handle_tools_call`` once the shared shell has applied the
    server arguments, judged the scope gate, merged a retry's answers and
    charged the rate limits, so this is only the call: the kernel's
    ``dispatch`` with the request's principal, a refusal answered through
    ``KERNEL_REFUSALS``, a ``not_found`` worded as the drf-services path words
    it, and the presented value as the tool result.
    """
    dispatched, page = page_request(binding, arguments, context.config)
    scene = KernelRefusalScene(
        arguments=arguments,
        config=context.config,
        conventions=context.conventions,
        prior=prior,
        context=context,
        span=otel_span,
    )
    try:
        result = dispatch(
            binding.spec,
            principal=context.token.user,
            arguments=dispatched,
            pool_seeds=context.pool_seeds,
            unknown_arguments=binding.unknown_arguments,
            progress=context.progress,
            call_context=call_context(
                carry_token(context.http_request, context.token), binding.name
            ),
        )
    except (DispatchError, ServiceError) as exc:
        return on_the_wire(answer_kernel_refusal(exc, scene))
    if result.kind == "not_found":
        return not_found_result(binding).to_dict()
    payload: Any = present_tool_result(binding, result, page)
    return tool_result(
        binding, payload, output_format=params.get("outputFormat"), config=context.config
    ).to_dict()


__all__ = ["run_kernel_tool"]
