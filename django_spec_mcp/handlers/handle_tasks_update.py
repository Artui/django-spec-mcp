from __future__ import annotations

from dataclasses import replace
from typing import Any

from django_spec_mcp._compat.tracing import span
from django_spec_mcp.handlers.admit_task_call import admit_task_call
from django_spec_mcp.handlers.tasks_utils import (
    declares_tasks_extension,
    missing_capability_error,
    owned_task,
    resolve_task_id,
)
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import span_attrs
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.subscriptions.publish_after_task import publish_task_status
from django_spec_mcp.tasks.resume_task import resume_task
from django_spec_mcp.tasks.transition_task import transition_task
from django_spec_mcp.tasks.types.task_record import TaskRecord
from django_spec_mcp.tasks.types.task_store import TaskStore
from django_spec_mcp.tasks.utils import input_request_key, now_iso
from django_spec_mcp.types.elicitation_key import ELICITATION_KEY
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.task_status import TaskStatus


def handle_tasks_update(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Deliver the client's answers to a task's outstanding input requests."""
    if not declares_tasks_extension(context):
        return missing_capability_error()
    task_id: str | JsonRpcError = resolve_task_id(params)
    if isinstance(task_id, JsonRpcError):
        return task_id
    assert isinstance(params, dict)  # noqa: S101 — resolve_task_id proved it

    responses: Any = params.get("inputResponses")
    if not isinstance(responses, dict):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            "'inputResponses' is required and must be an object keyed by request id",
        )

    owned = owned_task(task_id, context)
    if isinstance(owned, JsonRpcError):
        return owned
    store, record = owned

    if record.status is not TaskStatus.INPUT_REQUIRED:
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            f"Task {task_id!r} is not awaiting input (status: {record.status.value}).",
        )

    outstanding: dict[str, Any] = record.task.input_requests or {}
    accepted: dict[str, Any] = {k: v for k, v in responses.items() if k in outstanding}
    merged: dict[str, Any] = {**record.input_responses, **accepted}
    remaining: dict[str, Any] = {k: v for k, v in outstanding.items() if k not in merged}

    asked: str = input_request_key(record.input_round)
    if record.input_round >= 1 and set(outstanding) == {asked} and asked in accepted:
        return _resume(store, replace(record, input_responses=merged), accepted, context)

    updated: TaskRecord = replace(record, input_responses=merged).with_task(
        status=TaskStatus.WORKING if not remaining else TaskStatus.INPUT_REQUIRED,
        input_requests=remaining or None,
        last_updated_at=now_iso(),
        status_message=None if not remaining else record.task.status_message,
    )
    store.save(updated)
    publish_task_status(context.subscriptions, updated)
    return {}


def _resume(
    store: TaskStore,
    record: TaskRecord,
    answered: dict[str, Any],
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Merge the answer into the task's arguments and queue it again."""
    binding: Any = context.tools.get(record.tool_name)
    arguments: dict[str, Any] = record.arguments
    if binding is not None:
        answer: Any = next(iter(answered.values()), None)
        params: dict[str, Any] = {"inputResponses": {ELICITATION_KEY: answer}}
        with span("mcp.tools.call", attributes=span_attrs(binding.name, context)):
            refusal, arguments = admit_task_call(binding, arguments, context, params=params)
        if isinstance(refusal, JsonRpcError):
            return refusal
        if refusal is not None:
            transition_task(
                store,
                record.task_id,
                status=TaskStatus.COMPLETED,
                result=refusal,
                broker=context.subscriptions,
            )
            return {}
    resume_task(
        store,
        context.task_executor,
        replace(record, arguments=arguments),
        broker=context.subscriptions,
    )
    return {}


__all__ = ["handle_tasks_update"]
