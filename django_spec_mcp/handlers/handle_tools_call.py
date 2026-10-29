from __future__ import annotations

from typing import Any

from django_spec_mcp._compat.tracing import span
from django_spec_mcp.elicitation.types.resolved_input import ResolvedInput
from django_spec_mcp.handlers.input_dispatch import (
    refusal_result,
)
from django_spec_mcp.handlers.invalidation_dispatch import announce_invalidations
from django_spec_mcp.handlers.kernel_utils import admit_kernel_tool_call
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
    """Invoke a registered tool by name.

    The kernel owns the run: the spec's argument check, its permissions, the
    service or selector and the presenter (``run_kernel_tool``). This layer
    keeps the transport shell: the binding's scope gate and rate limits, a
    retry's answers, the result ceiling and
    ``structuredContent``.
    """
    if not isinstance(params, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "tools/call params must be an object")
    target = resolve_tool_call(params, context)
    if isinstance(target, JsonRpcError):
        return target
    binding, arguments_raw = target

    # Ordering: after argument-shape validation so a malformed call is rejected
    # outright rather than queued, and before dispatch because a task exists
    # precisely so the dispatch does not happen here.
    as_task: dict[str, Any] | JsonRpcError | None = maybe_create_task(
        binding, arguments_raw, context
    )
    if as_task is not None:
        return as_task

    # The ceiling is applied once, here, rather than at the three dispatch
    # paths' several ``build_tool_result`` sites, so it measures the finished
    # result (both copies of the payload) rather than a renderer's intermediate.
    dispatched, ran_with = _dispatch_tool_call(binding, params, arguments_raw, context)
    result: dict[str, Any] | JsonRpcError = enforce_result_ceiling(
        dispatched,
        max_result_bytes=resolve_bound(binding.max_result_bytes, context.config.max_result_bytes),
        label=f"Tool {binding.name!r}",
    )
    # After the ceiling, so a result too large to return does not announce a
    # change the client cannot then read back. With the arguments the tool ran
    # with, a retry's answers merged in: announced with the arguments as sent,
    # an answer moving the row named the row it moved from
    # (``test_the_invalidation_names_the_row_an_answer_moved_to``).
    announce_invalidations(binding, result, ran_with, context)
    return result


def _dispatch_tool_call(
    binding: KernelToolBinding,
    params: dict[str, Any],
    arguments_raw: dict[str, Any],
    context: MCPCallContext,
) -> tuple[dict[str, Any] | JsonRpcError, dict[str, Any]]:
    """Dispatch a resolved binding; return the raw result and its arguments.

    Split out of ``handle_tools_call`` so the size ceiling wraps every
    return at a single point. The async sibling splits the same way, plus the
    deadline.

    The arguments returned are the ones the tool ran with, which on a retry
    carry the answers merged over the arguments as sent, for the invalidation
    the caller announces.
    """
    # Scoped to the dispatch portion, after binding resolution, so cheap
    # validation rejections don't generate noise. A no-op without
    # ``opentelemetry-api``.
    with span(
        "mcp.tools.call",
        attributes=span_attrs(binding.name, context),
    ) as otel_span:
        # The binding's ``permissions``, its scope gate, before a retry's
        # answers are read, since a denied caller is told so first.
        allowed, required_scopes = judge_tool_permissions(binding, context)
        if not allowed:
            return forbidden(required_scopes), arguments_raw

        # On a retry of a call that asked the user something, the answer arrives
        # as ``inputResponses`` and becomes an ordinary argument here — the
        # whole of what the service ever sees of the exchange. A declined or
        # cancelled answer merges nothing, and is answered after the rate
        # limit below, as any call the service does not run is. Merged with no
        # ``requestState`` too, so a first call can carry one.
        prior: ResolvedInput = resolve_call_input(binding, params, arguments_raw, context)
        # Applied again over what the answers produced, since the merge takes
        # any key: an answer naming a bound argument would otherwise replace the
        # server's value (``test_an_answer_cannot_replace_a_bound_value``).
        arguments_raw = apply_tool_server_arguments(binding, prior.arguments)

        # The spec's own checks, asked here on the arguments the tool will run
        # with: after the answers are merged, so a route an answer names is the
        # one judged, and before the charge and the shape check inside
        # dispatch, so a caller they refuse is neither charged
        # (``test_a_caller_the_spec_refuses_is_not_charged``) nor told which
        # argument it left out
        # (``test_a_caller_the_spec_denies_is_refused_before_the_argument_is_checked``).
        # ``admit_kernel_call`` says what the checks are shown and how often
        # they are asked: the grant it returns is what dispatch runs under, so
        # when the arguments pass the shape check dispatch does not ask them
        # again, and a streamed call's pre-flight asks them once more.
        admission = admit_kernel_tool_call(binding, arguments_raw, context)
        if isinstance(admission, JsonRpcError):
            return admission, arguments_raw

        # After every check, so a caller one denies is never charged.
        retry_after: int | None = consume_rate_limits(
            effective_rate_limits(binding, context), context.http_request, context.token
        )
        if retry_after is not None:
            return rate_limited(retry_after), arguments_raw

        # Charged, as before: a declined answer is a call the client made
        # (``test_a_caller_declining_the_question_is_charged_once``).
        if prior.refused_with is not None:
            return refusal_result(prior.refused_with), arguments_raw

        return (
            run_kernel_tool(binding, params, arguments_raw, admission, prior, context, otel_span),
            arguments_raw,
        )


__all__ = ["handle_tools_call"]
