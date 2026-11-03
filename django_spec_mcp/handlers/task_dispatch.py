from __future__ import annotations

from typing import Any

from django_spec_mcp._compat.tracing import span
from django_spec_mcp.elicitation.can_ask_client import can_ask_client
from django_spec_mcp.handlers.admit_task_call import admit_task_call
from django_spec_mcp.handlers.kernel_utils import KernelAdmission
from django_spec_mcp.handlers.tasks_utils import (
    declares_tasks_extension,
    missing_capability_error,
)
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import span_attrs
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.task import Task
from django_spec_mcp.tasks.create_task import create_task
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.result_type import ResultType
from django_spec_mcp.types.task_policy import TaskPolicy


def maybe_create_task(
    binding: Any,
    arguments: dict[str, Any],
    context: MCPCallContext,
    *,
    admission: KernelAdmission | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | JsonRpcError | None:
    """Answer a tool call with a task handle, if this call should get one.

    Returns ``None`` — the overwhelmingly common case — when it should not, and
    the caller runs the tool inline. Anything else is the response.

    A task worker's run of a task (``context.in_task_worker``) is never one:
    it is the work the task was created for.

    **Three conditions, and all three must hold**: the binding opts in
    ([`TaskPolicy`][django_spec_mcp.types.TaskPolicy]), the server can actually
    run tasks, and the client declared the extension **on this request**. Each failure
    answers differently. A binding that says no runs inline. A server that cannot run
    tasks runs inline unless the policy is ``REQUIRED``, which makes it a
    misconfiguration on this side — ``-32603``, because blaming the client's request for
    a missing executor sends a competent client round a loop it cannot win. A client
    that did not declare runs inline unless the policy is ``REQUIRED``, which is exactly
    the ``-32021`` case the spec describes as a server *"unable to service a request ...
    without returning CreateTaskResult"*.

    **Permissions are checked here, before anything is created.** A task is
    durable queued work carrying the caller's authorization context; creating
    one for a caller who may not run the tool would queue a denied call and let
    its permission check fail later, in a worker, where the ``403`` has nowhere
    to go.

    **Rate limits are charged here, and this is the only place they are
    charged.** They follow the permission reasoning but are *consumed* rather
    than tested, so the charge has to happen exactly once — and the ordinary
    inline site cannot serve, because this function answers before dispatch is
    ever reached. The worker replays the call under
    ``enforce_rate_limits=False``, so it charges nothing; the request that asked
    for the task is what carries the caller's address and token, and is what
    pays. Without the charge here, declaring the tasks extension would be a
    self-service exemption from every quota a task-shaped tool configures.

    **A retry's answers are merged here, so the task is created with them.** The
    worker replays the recorded arguments and nothing else, with no
    ``inputResponses`` to read, so answers left out of the record are answers the
    task never sees: it would run without them and degrade to an ``isError``
    ``input_required`` result. ``params`` is the call's, and the merge is the
    inline one (``resolve_call_input``, then the binding's ``server_arguments``
    laid over again, since an answer may name any key). It runs after every
    route that sends the call inline and after the scope gate, so no call
    resolves its answers twice and a denied caller is told before they are read.
    Absent ``params`` merges nothing, which is how a caller with no call to read
    (the direct calls in ``tests/tasks/test_task_wire_shape.py``) asks. A declined or
    cancelled answer is answered after the charge, as inline, and creates no
    task. The merged arguments stay in the record for the task's lifetime, like
    any other argument.

    **From the scope gate on, it runs inside an ``mcp.tools.call`` span**, the one
    the inline call opens around the same checks, so a denial, a refused charge or
    a declined answer is traced whichever path answered it. A call that becomes a
    task is traced twice: its creation here, and its run in the worker, which
    re-enters the inline path.

    ``admission`` is the one a streamed call's pre-flight made, which task
    creation takes instead of asking the spec's checks again, so a seed a check
    names resolves once before the worker
    (``test_a_streamed_task_call_resolves_a_checks_seed_once_before_the_worker``).
    It is the same question only while nothing was carried in: the pre-flight asks
    on the client's ``arguments`` with the binding's ``server_arguments`` laid
    over, and a retry's answers are merged after, so a call that carried any is
    asked afresh on what the worker will run with
    (``test_a_streamed_task_retry_is_admitted_afresh``). Only the async view hands
    one in; the sync ``handle_tools_call`` has no pre-flight and asks here.
    """
    # A worker's run is the task this decision already made, so it runs, under
    # either policy: asked again, a ``REQUIRED`` tool's run met a worker that
    # declares no extension and was refused ``-32021``
    # (``test_a_required_tool_runs_as_a_task_too``, both paths).
    if context.in_task_worker:
        return None
    policy: TaskPolicy = getattr(binding, "task_policy", TaskPolicy.FORBIDDEN)
    if policy is TaskPolicy.FORBIDDEN:
        return None

    # Asked of every client, declaring or not, since the extension cannot
    # help a server with nothing to run a task on
    # (test_a_required_tool_without_a_server_to_run_it_blames_the_server_whatever_was_declared).
    store = context.tasks
    executor = context.task_executor
    if store is None or executor is None:
        if policy is TaskPolicy.REQUIRED:
            return JsonRpcError(
                JsonRpcErrorCode.INTERNAL_ERROR,
                f"Tool {binding.name!r} is registered to run only as a task, but this "
                "server has no task store and executor configured. Pass task_store= "
                "and task_executor= to MCPServer(...), or relax the tool's task_policy.",
            )
        return None

    # Before the scope gate and the spec's checks, so a client that did not
    # declare is told -32021 even by a tool that would refuse it
    # (test_the_capability_is_asked_before_the_scope_gate_and_the_spec_s_checks).
    if not declares_tasks_extension(context):
        if policy is TaskPolicy.REQUIRED:
            return missing_capability_error()
        return None

    # From the scope gate on, inside the span the inline call opens, so an
    # outcome the two paths share is traced on both: a denial, a refused charge
    # or a declined answer here is the inline call's own answer
    # (``test_a_task_path_refusal_is_answered_inside_the_span``). The checks
    # before it are cheap routing, which opens no span inline either.
    with span("mcp.tools.call", attributes=span_attrs(binding.name, context)):
        answer, arguments = admit_task_call(
            binding, arguments, context, admission=admission, params=params
        )
        if answer is not None:
            return answer
        task: Task = create_task(
            store=store,
            executor=executor,
            tool_name=binding.name,
            arguments=arguments,
            token=context.token,
            ttl_ms=context.config.task_ttl_ms,
            poll_interval_ms=context.config.task_poll_interval_ms,
            # Recorded, since the worker has no request to read it from: it is
            # what lets the worker park the task rather than complete it with an
            # ``isError`` result when the service asks
            # (``test_a_client_that_cannot_be_asked_gets_the_result_it_gets_today``).
            can_elicit=can_ask_client(context.client_capabilities),
        )
    # ``resultType`` is set explicitly and only here: the envelope stamps
    # ``complete`` on anything that has not declared itself, and the spec
    # forbids ``task`` on any result other than this one.
    return {"resultType": ResultType.TASK.value, **task.to_dict()}


__all__ = ["maybe_create_task"]
