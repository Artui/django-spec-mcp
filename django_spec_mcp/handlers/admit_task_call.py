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
    """Ask what a call is asked before it is queued as a task; return the answer
    and the arguments to queue it with.

    The answer is ``None`` when the call may be queued, and otherwise the response:
    a denial, a refused charge, or the tool result for a declined answer. The
    arguments are the call's with a retry's answers merged in and the binding's
    ``server_arguments`` laid over again.

    Two callers queue work, and both ask this: task creation
    (``maybe_create_task``), where ``params`` is the ``tools/call``, and an answer
    that resumes a waiting task (``handle_tasks_update``), where ``params`` carries
    the answer as a retry's ``inputResponses`` would. So an answer delivered to a
    waiting task meets the scope gate, the spec's checks on the merged arguments,
    the charge and the refusal exactly as one carried into task creation does.
    ``admission`` is a streamed call's pre-flight, which only task creation has.
    """
    # The binding's scope gate, through the helper the inline call uses.
    allowed, required_scopes = judge_tool_permissions(binding, context)
    if not allowed:
        return forbidden(required_scopes), arguments

    # A retry's answers become ordinary arguments here, as inline: the record
    # holds them, since the worker reads nothing else
    # (``test_a_retry_that_runs_as_a_task_stores_and_runs_its_answer``). Applied
    # again over what the answers produced, since the merge takes any key
    # (``test_an_answer_cannot_replace_a_bound_value_in_a_task``). Under the
    # call's dict, which the checks below are handed too
    # (``test_a_task_is_judged_under_the_call_s_dict``); their admission is not
    # kept, so the dict reaches no dispatch, and the worker builds its own.
    dispatched = tool_call_context(binding, context)
    prior: ResolvedInput | None = None
    if params is not None:
        prior = resolve_call_input(binding, params, arguments, context)
        arguments = apply_tool_server_arguments(
            binding, prior.arguments, dispatch_context=dispatched
        )

    # The spec's own checks, which the binding's ``permissions`` above do not
    # hold, as the inline call asks them before its charge: without this the
    # plain route queued a call its worker would refuse, where the streamed
    # route's pre-flight answered ``403``
    # (``test_a_caller_the_spec_refuses_gets_no_task``). Asked on the merged
    # arguments, so an answer that names a route is the one judged
    # (``test_a_caller_refused_on_the_merged_arguments_gets_no_task``). Its grant is
    # not kept, since the worker's replay is a later call, judged again. A
    # streamed call's pre-flight has asked already, on the arguments as sent,
    # and is not asked twice unless answers were carried in. Each part has the
    # test that fails without it: with the whole parenthesised disjunct gone,
    # ``test_a_streamed_task_retry_is_admitted_afresh`` (the carried answers are
    # never judged); with only ``bool(prior.carried)`` gone,
    # ``test_a_streamed_task_call_with_nothing_carried_is_not_asked_twice`` (a
    # call carrying nothing is asked again); with only ``prior is not None``
    # gone, a direct call with no ``params``
    # (``test_a_pre_flight_admission_stands_when_no_call_is_handed_in``) reads
    # ``carried`` off ``None``.
    if admission is None or (prior is not None and bool(prior.carried)):
        admitted = admit_kernel_tool_call(binding, arguments, context, dispatched)
        if isinstance(admitted, JsonRpcError):
            return admitted, arguments

    # After the permission checks and before anything durable exists: a denied
    # caller should not spend a quota, and a caller who has spent one should
    # not be told so only once a record has been written and queued.
    retry_after: int | None = consume_rate_limits(
        effective_rate_limits(binding, context), context.http_request, context.token
    )
    if retry_after is not None:
        return rate_limited(retry_after), arguments

    # After the charge, as inline: a declined answer is a call the client made
    # (``test_a_declined_answer_creates_no_task_and_is_charged_once``).
    if prior is not None and prior.refused_with is not None:
        return refusal_result(prior.refused_with), arguments

    return None, arguments


__all__ = ["admit_task_call"]
