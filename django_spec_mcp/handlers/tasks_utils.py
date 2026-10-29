"""Shared checks for the three ``tasks/*`` handlers."""

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
    """Whether *this request* declared the tasks extension.

    Per request, never remembered: the spec forbids answering with a task to a
    client that did not declare on that request, *"regardless of prior
    declarations"*, and the stateless revision leaves nowhere to remember one.

    A legacy-era request declares nothing — its capabilities arrived once, at
    ``initialize`` — so legacy clients never reach the task path without an era
    branch here.
    """
    extensions: Any = context.client_capabilities.get("extensions")
    return isinstance(extensions, dict) and TASKS_EXTENSION_ID in extensions


def missing_capability_error() -> JsonRpcError:
    """The ``-32021`` a non-declaring client gets from anything task-shaped.

    **Deliberately not the code the extension document prints.** That text says
    ``-32003`` while annotating it ``MISSING_REQUIRED_CLIENT_CAPABILITY`` — the
    same constant the ratified core schema allocates as **-32021**. ``-32003``
    now sits inside ``-32000..-32019``, which the core spec reserves for
    implementations, saying *"the specification will never define codes in this
    sub-range"*; emitting it would mean a code that means nothing across
    implementations. It is also one of the two codes this package burned when
    its error numbering was corrected, so an older client reads it as "not
    found".

    ``data.requiredCapabilities`` follows the extension's example shape, which
    is what a client reads to learn what to declare.
    """
    return JsonRpcError(
        JsonRpcErrorCode.MISSING_REQUIRED_CLIENT_CAPABILITY,
        "Missing required client capability",
        data={"requiredCapabilities": {"extensions": {TASKS_EXTENSION_ID: {}}}},
    )


def unknown_task_error(task_id: Any) -> JsonRpcError:
    """The one answer for "no such task", whatever the real reason.

    Never existed, expired, or belongs to somebody else — all ``-32602``, all
    with the same message. The uniformity is the security property: with no
    ``tasks/list`` and no session to scope by, an id's unguessability is the
    containment boundary, and distinguishing "not yours" from "not found" would
    make any endpoint an oracle for which ids are real.
    """
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
    """Whether the calling principal is the one the task was created for.

    The same comparison session ownership makes, against the same derived id. A
    second layer under ``unknown_task_error``, and the one that matters when
    an id leaks through a log, a proxy or a shared client.
    """
    return record.principal_id == principal_for_token(context.token)


def owned_task(
    task_id: str, context: MCPCallContext
) -> tuple[TaskStore, TaskRecord] | JsonRpcError:
    """The store and the record ``task_id`` names, if the caller owns it.

    A server running no tasks, an id the store does not hold and a task another
    principal created all get the one ``unknown_task_error``, so no answer
    tells them apart.
    """
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
