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
    """Move a task whose input is answered back to ``working`` and queue it again.

    ``record`` is the task as ``tasks/update`` left it, its answers merged into its
    arguments. The worker that parked it cleared ``enqueued`` as it claimed it, so
    a stale delivery of the first run found nothing to claim; this sets it again,
    **before** the hand-off rather than after as ``create_task`` does, since an
    executor may start the worker before ``enqueue`` returns, and a worker finding
    the flag still clear would leave the task ``working`` for good.

    A hand-off that fails is reported as a ``failed`` task, as at creation: the
    client already holds the handle, and polls it to find out.
    """
    working: TaskRecord = replace(record, enqueued=True).with_task(
        status=TaskStatus.WORKING,
        input_requests=None,
        status_message=None,
        last_updated_at=now_iso(),
    )
    store.save(working)
    # Answering changes the task's status, so a subscriber watching it hears,
    # exactly as it would for any other transition.
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
            status_message=f"The task could not be queued again after its input was answered: {exc}",
            broker=broker,
        )


__all__ = ["resume_task"]
