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
    """Report a task's current state. **The only way to retrieve a result.**

    Polling is the whole retrieval story — there is no ``tasks/result`` that
    blocks until terminal, and ``pollIntervalMs`` on the record is how the
    server asks for a sane cadence.

    The response is the task itself; which extra field it carries follows from the
    status, and [`Task.to_dict`][django_spec_mcp.protocol.types.task.Task.to_dict]
    owns that correspondence, so this handler never branches on status.

    ``resultType`` is ``"complete"``, not ``"task"``. The distinction is
    normative and easy to get backwards: ``"task"`` marks *the result that hands
    out a task handle in place of the answer*, nothing else, so stamping it here
    would tell the client it had been handed a second task.

    Deliberately **not** cacheable: a task's whole purpose is to change, and the
    client is asking precisely because it wants to know whether it has.
    """
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
