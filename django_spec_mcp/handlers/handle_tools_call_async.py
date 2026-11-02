from __future__ import annotations

import asyncio
from typing import Any

from asgiref.sync import sync_to_async

from django_spec_mcp._compat.acall import acall
from django_spec_mcp._compat.tracing import span
from django_spec_mcp.elicitation.types.resolved_input import ResolvedInput
from django_spec_mcp.handlers.input_dispatch import (
    refusal_result,
)
from django_spec_mcp.handlers.invalidation_dispatch import announce_invalidations_async
from django_spec_mcp.handlers.kernel_utils import KernelAdmission, admit_kernel_tool_call
from django_spec_mcp.handlers.resolve_call_input import resolve_call_input
from django_spec_mcp.handlers.run_kernel_tool_async import run_kernel_tool_async
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
    run_with_deadline,
    span_attrs,
)
from django_spec_mcp.output.error_tool_result import build_error_tool_result
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode


async def handle_tools_call_async(
    params: dict[str, Any] | None,
    context: MCPCallContext,
    *,
    admission: KernelAdmission | None = None,
) -> dict[str, Any] | JsonRpcError:
    """Async sibling of ``handle_tools_call``.

    The tool runs through the kernel's ``adispatch`` (``run_kernel_tool_async``),
    which awaits async-native callables and bridges sync ones off the event
    loop. The transport shell — the binding's scope gate and rate limits,
    ``structuredContent`` — stays here, its blocking parts
    through ``acall``.

    ``admission`` is the one a streamed call's pre-flight already made, for the
    arguments the client sent. It is reused only when no retry answer was merged
    into them, and the spec's checks are asked afresh otherwise, so each resolves
    its seeds once per call. Task creation takes it too, ahead of any retry's
    answers being merged, so a streamed call that runs as a task resolves a seed
    a check names once before the worker. Only the transport's stream hands one in;
    the default asks the checks here, as every other caller expects.
    """
    if not isinstance(params, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "tools/call params must be an object")
    target = resolve_tool_call(params, context)
    if isinstance(target, JsonRpcError):
        return target
    binding, arguments_raw = target

    # Placed as in the sync sibling, and deliberately *outside* the deadline:
    # creating a task is a store write and a queue hand-off, not the work, and
    # timing it out would abandon a task already durably created. Taken through
    # the thread-sensitive executor because it reads the cache and runs the
    # spec's permission checks, both of which reach code Django marks
    # async-unsafe (a database-backed cache raises; a check may run a query).
    as_task: dict[str, Any] | JsonRpcError | None = await sync_to_async(
        maybe_create_task, thread_sensitive=True
    )(binding, arguments_raw, context, admission=admission)
    if as_task is not None:
        return as_task

    try:
        dispatched, ran_with = await run_with_deadline(
            _dispatch_tool_call_async(binding, params, arguments_raw, context, admission),
            resolve_bound(binding.dispatch_timeout, context.config.dispatch_timeout),
        )
    except asyncio.TimeoutError:  # noqa: UP041 — 3.10 keeps this distinct from builtins
        # A tool *execution* failure, not a malformed request, so an ``isError``
        # result the model can act on. The dispatch keeps running — see
        # ``run_with_deadline`` — so this ends the client's wait, not the work.
        return build_error_tool_result(
            f"Tool {binding.name!r} exceeded this server's dispatch deadline and was "
            "abandoned. It may still be running. Narrow the request — tighten a "
            "filter, lower 'limit' — and try again.",
            error_type="timeout",
        ).to_dict()
    bounded: dict[str, Any] | JsonRpcError = enforce_result_ceiling(
        dispatched,
        max_result_bytes=resolve_bound(binding.max_result_bytes, context.config.max_result_bytes),
        label=f"Tool {binding.name!r}",
    )
    # See the sync sibling: after the ceiling, so an oversized result does not
    # announce a change the client cannot read back, and with the arguments the
    # tool ran with, a retry's answers merged in.
    await announce_invalidations_async(binding, bounded, ran_with, context)
    return bounded


async def _dispatch_tool_call_async(
    binding: KernelToolBinding,
    params: dict[str, Any],
    arguments_raw: dict[str, Any],
    context: MCPCallContext,
    preflight: KernelAdmission | None,
) -> tuple[dict[str, Any] | JsonRpcError, dict[str, Any]]:
    """Dispatch a resolved binding; return the result and its arguments.

    Split out of ``handle_tools_call_async`` so one deadline covers the
    whole dispatch — permissions, rate limits, the spec run and rendering — and
    one size check sees the finished result. The arguments returned are the
    ones the tool ran with, as in the sync sibling. ``preflight`` is the
    admission a streamed call's pre-flight made, if any.
    """
    with span("mcp.tools.call", attributes=span_attrs(binding.name, context)) as otel_span:
        # See the sync sibling: the binding's scope gate, before a retry's
        # answers are read. Off the loop, since a permission may query.
        allowed, required_scopes = await acall(judge_tool_permissions, binding, context)
        if not allowed:
            return forbidden(required_scopes), arguments_raw

        # See the sync sibling: a retry's answers become ordinary arguments
        # before anything else looks at them, and a declined one is answered
        # after the rate limit.
        prior: ResolvedInput = resolve_call_input(binding, params, arguments_raw, context)
        # See the sync sibling: applied again, since the merge takes any key.
        arguments_raw = apply_tool_server_arguments(binding, prior.arguments)

        # See the sync sibling: the spec's own checks, on the arguments the
        # tool will run with and before the charge, off the loop because a
        # check may query.
        # A pre-flight's admission was made on what the client sent, so it
        # stands only while nothing was merged in
        # (``test_a_streamed_retry_with_merged_answers_is_admitted_afresh``);
        # reusing it keeps one resolution of each seed a check names, and the
        # call context the carrier is bound to by identity.
        admission: KernelAdmission | JsonRpcError
        if preflight is not None and not prior.carried:
            admission = preflight
        else:
            admission = await acall(admit_kernel_tool_call, binding, arguments_raw, context)
        if isinstance(admission, JsonRpcError):
            return admission, arguments_raw

        # See the sync sibling: after every check, so a caller one denies is
        # never charged.
        retry_after: int | None = await acall(
            consume_rate_limits,
            effective_rate_limits(binding, context),
            context.http_request,
            context.token,
        )
        if retry_after is not None:
            return rate_limited(retry_after), arguments_raw

        if prior.refused_with is not None:
            return refusal_result(prior.refused_with), arguments_raw

        return (
            await run_kernel_tool_async(
                binding, params, arguments_raw, admission, prior, context, otel_span
            ),
            arguments_raw,
        )


__all__ = ["handle_tools_call_async"]
