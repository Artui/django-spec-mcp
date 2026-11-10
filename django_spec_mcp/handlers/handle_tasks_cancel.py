from __future__ import annotations

from typing import Any

from django_spec_mcp.handlers.tasks_utils import (
    declares_tasks_extension,
    missing_capability_error,
    owned_task,
    resolve_task_id,
)
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.tasks.transition_task import transition_task
from django_spec_mcp.types.task_status import TaskStatus


def handle_tasks_cancel(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Signal that a task should stop."""
    if not declares_tasks_extension(context):
        return missing_capability_error()
    task_id: str | JsonRpcError = resolve_task_id(params)
    if isinstance(task_id, JsonRpcError):
        return task_id

    owned = owned_task(task_id, context)
    if isinstance(owned, JsonRpcError):
        return owned
    store, record = owned

    transition_task(
        store,
        task_id,
        status=TaskStatus.CANCELLED,
        status_message="Cancelled at the client's request.",
        broker=context.subscriptions,
    )
    return {}


__all__ = ["handle_tasks_cancel"]
