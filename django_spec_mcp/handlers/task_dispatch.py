from __future__ import annotations

from typing import Any

from django_spec_mcp.handlers.kernel_utils import admit_kernel_tool_call
from django_spec_mcp.handlers.tasks_utils import (
    declares_tasks_extension,
    missing_capability_error,
)
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import (
    consume_rate_limits,
    effective_rate_limits,
    forbidden,
    judge_tool_permissions,
    rate_limited,
)
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

    # The binding's scope gate, through the helper the inline call uses.
    allowed, required_scopes = judge_tool_permissions(binding, context)
    if not allowed:
        return forbidden(required_scopes)

    # The spec's own checks, which the binding's ``permissions`` above do not
    # hold, as the inline call asks them before its charge: without this the
    # plain route queued a call its worker would refuse, where the streamed
    # route's pre-flight answered ``403``
    # (``test_a_caller_the_spec_refuses_gets_no_task``). Its grant is not kept,
    # since the worker's replay is a later call, judged again.
    admitted = admit_kernel_tool_call(binding, arguments, context)
    if isinstance(admitted, JsonRpcError):
        return admitted

    # After the permission checks and before anything durable exists: a denied
    # caller should not spend a quota, and a caller who has spent one should
    # not be told so only once a record has been written and queued.
    retry_after: int | None = consume_rate_limits(
        effective_rate_limits(binding, context), context.http_request, context.token
    )
    if retry_after is not None:
        return rate_limited(retry_after)

    task: Task = create_task(
        store=store,
        executor=executor,
        tool_name=binding.name,
        arguments=arguments,
        token=context.token,
        ttl_ms=context.config.task_ttl_ms,
        poll_interval_ms=context.config.task_poll_interval_ms,
    )
    # ``resultType`` is set explicitly and only here: the envelope stamps
    # ``complete`` on anything that has not declared itself, and the spec
    # forbids ``task`` on any result other than this one.
    return {"resultType": ResultType.TASK.value, **task.to_dict()}


__all__ = ["maybe_create_task"]
