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

    ``record`` is the task as ``tasks/update`` read it, its answers merged into its
    arguments. Those two are saved from it; its round and the keys of its
    outstanding requests say which question the update answered, and nothing else
    is taken from it. Admitting the answer (the checks and the charge) ran after
    ``tasks/update`` read the task, so the task may since have been cancelled, have
    expired, or have been answered by a duplicate ``tasks/update`` whose worker ran,
    and that worker's service may have asked again, parking the task on the next
    round. It is read again, and one no longer waiting on the question this update
    answered is left as it is and queued nowhere
    (``test_a_task_ended_while_its_answer_is_admitted_stays_ended``,
    ``test_a_duplicate_answer_runs_the_service_once``,
    ``test_a_late_answer_leaves_the_next_rounds_question_in_place``). The read and
    the save are still two calls, the window ``transition_task`` accepts.

    The worker that parked the task cleared ``enqueued`` as it claimed it, so a
    stale delivery of the first run found nothing to claim; this sets it again,
    **before** the hand-off rather than after as ``create_task`` does, since an
    executor may start the worker before ``enqueue`` returns, and a worker finding
    the flag still clear would leave the task ``working`` for good
    (``test_the_answer_is_saved_before_the_task_is_queued``).

    A hand-off that fails is reported as a ``failed`` task, as at creation: the
    client already holds the handle, and polls it to find out.
    """
    current: TaskRecord | None = store.get(record.task_id)
    # Still waiting, and on the question this update read. In the package's own
    # parks the round and the key move together, so each conjunct past the first
    # has a case where it alone moved, in
    # ``test_an_answer_resumes_only_the_question_it_read``: ``status``, ``round``
    # and ``requests``. A missing record is the expired case of
    # ``test_a_task_ended_while_its_answer_is_admitted_stays_ended``.
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
