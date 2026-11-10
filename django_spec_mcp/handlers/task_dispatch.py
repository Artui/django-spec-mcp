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
    """Answer a tool call with a task handle, if this call should get one."""
    if context.in_task_worker:
        return None
    policy: TaskPolicy = getattr(binding, "task_policy", TaskPolicy.FORBIDDEN)
    if policy is TaskPolicy.FORBIDDEN:
        return None

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

    if not declares_tasks_extension(context):
        if policy is TaskPolicy.REQUIRED:
            return missing_capability_error()
        return None

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
            can_elicit=can_ask_client(context.client_capabilities),
        )
    return {"resultType": ResultType.TASK.value, **task.to_dict()}


__all__ = ["maybe_create_task"]
