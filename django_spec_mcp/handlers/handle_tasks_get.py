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


def handle_tasks_get(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Report a task's current state."""
    if not declares_tasks_extension(context):
        return missing_capability_error()
    task_id: str | JsonRpcError = resolve_task_id(params)
    if isinstance(task_id, JsonRpcError):
        return task_id

    owned = owned_task(task_id, context)
    if isinstance(owned, JsonRpcError):
        return owned
    _store, record = owned
    return record.to_wire().to_dict()


__all__ = ["handle_tasks_get"]
