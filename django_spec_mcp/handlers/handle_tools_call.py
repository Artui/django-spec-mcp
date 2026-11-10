from __future__ import annotations

from typing import Any

from django_spec_mcp._compat.tracing import span
from django_spec_mcp.elicitation.types.resolved_input import ResolvedInput
from django_spec_mcp.handlers.input_dispatch import (
    refusal_result,
)
from django_spec_mcp.handlers.invalidation_dispatch import announce_invalidations
from django_spec_mcp.handlers.kernel_utils import admit_kernel_tool_call, tool_call_context
from django_spec_mcp.handlers.resolve_call_input import resolve_call_input
from django_spec_mcp.handlers.run_kernel_tool import run_kernel_tool
from django_spec_mcp.handlers.task_dispatch import maybe_create_task
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import (
    apply_tool_server_arguments,
    consume_rate_limits,
    effective_rate_limits,
    enforce_result_ceiling,
    forbidden,
    judge_tool_permissions,
    rate_limited,
    resolve_bound,
    resolve_tool_call,
    span_attrs,
)
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode


def handle_tools_call(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Invoke a registered tool by name."""
    if not isinstance(params, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "tools/call params must be an object")
    target = resolve_tool_call(params, context)
    if isinstance(target, JsonRpcError):
        return target
    binding, sent = target
    arguments_raw = apply_tool_server_arguments(
        binding, sent, dispatch_context=tool_call_context(binding, context)
    )

    as_task: dict[str, Any] | JsonRpcError | None = maybe_create_task(
        binding, arguments_raw, context, params=params
    )
    if as_task is not None:
        return as_task

    dispatched, ran_with = _dispatch_tool_call(binding, params, arguments_raw, context)
    result: dict[str, Any] | JsonRpcError = enforce_result_ceiling(
        dispatched,
        max_result_bytes=resolve_bound(binding.max_result_bytes, context.config.max_result_bytes),
        label=f"Tool {binding.name!r}",
    )
    announce_invalidations(binding, result, ran_with, context)
    return result


def _dispatch_tool_call(
    binding: KernelToolBinding,
    params: dict[str, Any],
    arguments_raw: dict[str, Any],
    context: MCPCallContext,
) -> tuple[dict[str, Any] | JsonRpcError, dict[str, Any]]:
    """Dispatch a resolved binding; return the raw result and its arguments."""
    with span(
        "mcp.tools.call",
        attributes=span_attrs(binding.name, context),
    ) as otel_span:
        allowed, required_scopes = judge_tool_permissions(binding, context)
        if not allowed:
            return forbidden(required_scopes), arguments_raw

        prior: ResolvedInput = resolve_call_input(binding, params, arguments_raw, context)
        dispatched = tool_call_context(binding, context)
        arguments_raw = apply_tool_server_arguments(
            binding, prior.arguments, dispatch_context=dispatched
        )

        admission = admit_kernel_tool_call(binding, arguments_raw, context, dispatched)
        if isinstance(admission, JsonRpcError):
            return admission, arguments_raw

        retry_after: int | None = consume_rate_limits(
            effective_rate_limits(binding, context), context.http_request, context.token
        )
        if retry_after is not None:
            return rate_limited(retry_after), arguments_raw

        if prior.refused_with is not None:
            return refusal_result(prior.refused_with), arguments_raw

        return (
            run_kernel_tool(binding, params, arguments_raw, admission, prior, context, otel_span),
            arguments_raw,
        )


__all__ = ["handle_tools_call"]
