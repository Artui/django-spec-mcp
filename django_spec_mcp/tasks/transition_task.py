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
    """Move a task to ``status``, refusing to reopen a finished one.

    Returns the record as it now stands, or ``None`` if there is no such task.

    **The terminal guard is the whole reason this exists** rather than each
    caller writing its own ``store.save``. Two writers race by construction —
    the worker finishing the job and a ``tasks/cancel`` from the client — and
    no backend here locks, so without a guard a cancel landing just after
    completion would move a task from ``completed`` back to ``cancelled`` and
    tell the client its finished work never ran. The state machine is one-way:
    once ``completed`` / ``failed`` / ``cancelled``, a task stays there.

    A refused transition is **not an error**: the caller asked for something
    already decided, and the record it gets back says what was decided, which is
    what a cancel-after-completion should report. Callers needing to know
    whether they won compare the returned status to the one they asked for.

    ``lastUpdatedAt`` moves only on a transition that happened, so it stays an
    honest "when did this task last change".

    ``input_round`` sets the record's round with the transition, in the same save,
    and ``None`` (the default) keeps the round as it stands, where every other
    keyword's ``None`` clears its field: the round counts a task's questions over
    its lifetime, and a transition that does not ask one has nothing to say about
    it. A refused transition sets no round either
    (``test_a_transition_sets_the_round_only_when_asked_and_only_when_it_happens``).

    ``broker`` opts the transition into ``notifications/tasks``, published only
    for a transition that *happened* — a refused one changed nothing, and
    saying otherwise would have a subscriber re-read an unmoved status.
    """
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
