from __future__ import annotations

from typing import Any

from django_spec_mcp.auth.principal_for_token import principal_for_token
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.tasks.types.task_record import TaskRecord
from django_spec_mcp.tasks.types.task_store import TaskStore
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.tasks_extension_id import TASKS_EXTENSION_ID


def declares_tasks_extension(context: MCPCallContext) -> bool:
    """Whether *this request* declared the tasks extension."""
    extensions: Any = context.client_capabilities.get("extensions")
    return isinstance(extensions, dict) and TASKS_EXTENSION_ID in extensions


def missing_capability_error() -> JsonRpcError:
    """The ``-32021`` a non-declaring client gets from anything task-shaped."""
    return JsonRpcError(
        JsonRpcErrorCode.MISSING_REQUIRED_CLIENT_CAPABILITY,
        "Missing required client capability",
        data={"requiredCapabilities": {"extensions": {TASKS_EXTENSION_ID: {}}}},
    )


def unknown_task_error(task_id: Any) -> JsonRpcError:
    """The one answer for "no such task", whatever the real reason."""
    return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, f"Unknown task: {task_id!r}")


def resolve_task_id(params: dict[str, Any] | None) -> str | JsonRpcError:
    if not isinstance(params, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "params must be an object")
    task_id: Any = params.get("taskId")
    if not isinstance(task_id, str) or not task_id:
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "'taskId' is required and must be a string"
        )
    return task_id


def owned_by_caller(record: TaskRecord, context: MCPCallContext) -> bool:
    """Whether the calling principal is the one the task was created for."""
    return record.principal_id == principal_for_token(context.token)


def owned_task(
    task_id: str, context: MCPCallContext
) -> tuple[TaskStore, TaskRecord] | JsonRpcError:
    """The store and the record ``task_id`` names, if the caller owns it."""
    store = context.tasks
    if store is None:
        return unknown_task_error(task_id)
    record: TaskRecord | None = store.get(task_id)
    if record is None or not owned_by_caller(record, context):
        return unknown_task_error(task_id)
    return store, record


__all__ = [
    "declares_tasks_extension",
    "missing_capability_error",
    "owned_by_caller",
    "owned_task",
    "resolve_task_id",
    "unknown_task_error",
]
