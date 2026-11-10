from __future__ import annotations

from typing import Any

from django_spec_mcp.elicitation.types.resolved_input import ResolvedInput
from django_spec_mcp.handlers.input_dispatch import refusal_result
from django_spec_mcp.handlers.kernel_utils import (
    KernelAdmission,
    admit_kernel_tool_call,
    tool_call_context,
)
from django_spec_mcp.handlers.resolve_call_input import resolve_call_input
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import (
    apply_tool_server_arguments,
    consume_rate_limits,
    effective_rate_limits,
    forbidden,
    judge_tool_permissions,
    rate_limited,
)
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError


def admit_task_call(
    binding: Any,
    arguments: dict[str, Any],
    context: MCPCallContext,
    *,
    admission: KernelAdmission | None = None,
    params: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | JsonRpcError | None, dict[str, Any]]:
    """Ask what a call is asked before it is queued as a task."""
    allowed, required_scopes = judge_tool_permissions(binding, context)
    if not allowed:
        return forbidden(required_scopes), arguments

    dispatched = tool_call_context(binding, context)
    prior: ResolvedInput | None = None
    if params is not None:
        prior = resolve_call_input(binding, params, arguments, context)
        arguments = apply_tool_server_arguments(
            binding, prior.arguments, dispatch_context=dispatched
        )

    if admission is None or (prior is not None and bool(prior.carried)):
        admitted = admit_kernel_tool_call(binding, arguments, context, dispatched)
        if isinstance(admitted, JsonRpcError):
            return admitted, arguments

    retry_after: int | None = consume_rate_limits(
        effective_rate_limits(binding, context), context.http_request, context.token
    )
    if retry_after is not None:
        return rate_limited(retry_after), arguments

    if prior is not None and prior.refused_with is not None:
        return refusal_result(prior.refused_with), arguments

    return None, arguments


__all__ = ["admit_task_call"]
