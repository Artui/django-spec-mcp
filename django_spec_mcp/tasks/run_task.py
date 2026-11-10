from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from django_spec_mcp.auth.principal_for_token import principal_for_token
from django_spec_mcp.elicitation.fingerprint_request import fingerprint_request
from django_spec_mcp.elicitation.sign_request_state import sign_request_state
from django_spec_mcp.elicitation.types.request_state import RequestState
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.input_dispatch import TOOLS_CALL_METHOD
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from django_spec_mcp.tasks.report_task_progress import report_task_progress
from django_spec_mcp.tasks.transition_task import transition_task
from django_spec_mcp.tasks.types.task_record import TaskRecord
from django_spec_mcp.tasks.types.task_store import TaskStore
from django_spec_mcp.tasks.utils import input_request_key
from django_spec_mcp.types.elicitation_key import ELICITATION_KEY
from django_spec_mcp.types.result_type import ResultType
from django_spec_mcp.types.task_status import TaskStatus


def run_task(
    store: TaskStore,
    task_id: str,
    *,
    context_factory: Callable[[TaskRecord], MCPCallContext],
) -> None:
    """Execute a stored task and record its outcome."""
    record: TaskRecord | None = store.get(task_id)
    if record is None or not record.enqueued or record.status.is_terminal:
        return
    store.save(replace(record, enqueued=False))

    context: MCPCallContext = context_factory(record)
    context = replace(context, progress=report_task_progress(store, task_id))
    broker = context.subscriptions
    if record.can_elicit:
        context = replace(context, client_capabilities={"elicitation": {}})
    try:
        result: Any = handle_tools_call(_replay(record, context), context)
    except Exception as exc:  # noqa: BLE001 — the worker's last line of defence
        transition_task(
            store,
            task_id,
            status=TaskStatus.FAILED,
            status_message=f"The task raised an unhandled exception: {exc}",
            broker=broker,
        )
        raise

    if isinstance(result, JsonRpcError):
        transition_task(
            store,
            task_id,
            status=TaskStatus.FAILED,
            status_message=result.message,
            error=result.to_dict(),
            broker=broker,
        )
        return
    if result.get("resultType") == ResultType.INPUT_REQUIRED.value:
        _park(store, task_id, record.input_round + 1, result, broker)
        return
    transition_task(store, task_id, status=TaskStatus.COMPLETED, result=result, broker=broker)


def _replay(record: TaskRecord, context: MCPCallContext) -> dict[str, Any]:
    """The ``tools/call`` a run replays: the recorded call."""
    params: dict[str, Any] = {"name": record.tool_name, "arguments": record.arguments}
    if record.input_round:
        state = RequestState(
            principal=principal_for_token(context.token),
            fingerprint=fingerprint_request(
                TOOLS_CALL_METHOD, {"name": record.tool_name, "arguments": record.arguments}
            ),
            round=record.input_round,
        )
        params["requestState"] = sign_request_state(state)
    return params


def _park(
    store: TaskStore,
    task_id: str,
    asked: int,
    result: dict[str, Any],
    broker: SubscriptionBroker | None,
) -> None:
    """Move a task whose service asked to ``input_required``."""
    transition_task(
        store,
        task_id,
        status=TaskStatus.INPUT_REQUIRED,
        input_requests={input_request_key(asked): result["inputRequests"][ELICITATION_KEY]},
        input_round=asked,
        broker=broker,
    )


__all__ = ["run_task"]
