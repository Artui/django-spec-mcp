from __future__ import annotations

from dataclasses import replace
from typing import Any

from django_spec_mcp.subscriptions.publish_after_task import publish_task_status
from django_spec_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from django_spec_mcp.tasks.types.task_record import TaskRecord
from django_spec_mcp.tasks.types.task_store import TaskStore
from django_spec_mcp.tasks.utils import now_iso
from django_spec_mcp.types.task_status import TaskStatus


def transition_task(
    store: TaskStore,
    task_id: str,
    *,
    status: TaskStatus,
    status_message: str | None = None,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    input_requests: dict[str, Any] | None = None,
    input_round: int | None = None,
    broker: SubscriptionBroker | None = None,
) -> TaskRecord | None:
    """Move a task to ``status``, refusing to reopen a finished one."""
    record: TaskRecord | None = store.get(task_id)
    if record is None:
        return None
    if record.status.is_terminal:
        return record

    updated: TaskRecord = record.with_task(
        status=status,
        last_updated_at=now_iso(),
        status_message=status_message,
        result=result,
        error=error,
        input_requests=input_requests,
    )
    if input_round is not None:
        updated = replace(updated, input_round=input_round)
    store.save(updated)
    publish_task_status(broker, updated)
    return updated


__all__ = ["transition_task"]
