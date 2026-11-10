from __future__ import annotations

from typing import Any

from django_service_specs import DispatchError, ServiceError, adispatch

from django_spec_mcp._compat.acall import acall
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


async def run_kernel_tool_async(
    binding: KernelToolBinding,
    params: dict[str, Any],
    arguments: dict[str, Any],
    admission: KernelAdmission,
    prior: ResolvedInput,
    context: MCPCallContext,
    otel_span: Any,
) -> dict[str, Any] | JsonRpcError:
    """Async sibling of ``run_kernel_tool``, through ``adispatch``."""
    dispatched, page = page_request(binding, arguments, context.config)
    try:
        result = await adispatch(
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
        handler = exception_handler_for(exc, context.exception_map)
        if handler is not None:
            answer: ToolResult = await acall(
                answer_mapped_exception, handler, exc, config=context.config, span=otel_span
            )
            return answer.to_dict()
        if not isinstance(exc, DispatchError | ServiceError):
            raise
        scene = tool_refusal_scene(
            binding, arguments, prior, context, otel_span, admission.call_context
        )
        return on_the_wire(answer_kernel_refusal(exc, scene))
    if result.kind == "not_found":
        return not_found_result(binding).to_dict()
    bounded = await acall(
        bound_unpaginated_rows,
        binding,
        result,
        page,
        context.config,
        exception_map=context.exception_map,
        span=otel_span,
    )
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
        exception_map=context.exception_map,
        span=otel_span,
    )
    if isinstance(payload, ToolResult):
        return payload.to_dict()
    return tool_result(binding, payload, config=context.config).to_dict()


__all__ = ["run_kernel_tool_async"]
