from __future__ import annotations

from dataclasses import replace

from django_spec_mcp.subscriptions.publish_after_task import publish_task_status
from django_spec_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from django_spec_mcp.tasks.transition_task import transition_task
from django_spec_mcp.tasks.types.task_executor import TaskExecutor
from django_spec_mcp.tasks.types.task_record import TaskRecord
from django_spec_mcp.tasks.types.task_store import TaskStore
from django_spec_mcp.tasks.utils import now_iso
from django_spec_mcp.types.task_status import TaskStatus


def resume_task(
    store: TaskStore,
    executor: TaskExecutor | None,
    record: TaskRecord,
    *,
    broker: SubscriptionBroker | None = None,
) -> None:
    """Move a task whose input is answered back to ``working`` and queue it again."""
    current: TaskRecord | None = store.get(record.task_id)
    if (
        current is None
        or current.status is not TaskStatus.INPUT_REQUIRED
        or current.input_round != record.input_round
        or set(current.task.input_requests or {}) != set(record.task.input_requests or {})
    ):
        return
    working: TaskRecord = replace(
        current,
        arguments=record.arguments,
        input_responses=record.input_responses,
        enqueued=True,
    ).with_task(
        status=TaskStatus.WORKING,
        input_requests=None,
        status_message=None,
        last_updated_at=now_iso(),
    )
    store.save(working)
    publish_task_status(broker, working)

    try:
        if executor is None:
            raise RuntimeError("this server has no task executor")  # noqa: TRY301
        executor.enqueue(record.task_id)
    except Exception as exc:  # noqa: BLE001 — any broker failure, reported as one
        transition_task(
            store,
            record.task_id,
            status=TaskStatus.FAILED,
            status_message=(
                f"The task could not be queued again after its input was answered: {exc}"
            ),
            broker=broker,
        )


__all__ = ["resume_task"]
