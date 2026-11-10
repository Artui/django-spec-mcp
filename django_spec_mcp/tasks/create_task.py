from __future__ import annotations

from dataclasses import replace
from typing import Any

from django_spec_mcp.auth.principal_for_token import principal_for_token
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.protocol.types.task import Task
from django_spec_mcp.tasks.types.task_executor import TaskExecutor
from django_spec_mcp.tasks.types.task_record import TaskRecord
from django_spec_mcp.tasks.types.task_store import TaskStore
from django_spec_mcp.tasks.utils import new_task_id, now_iso
from django_spec_mcp.types.task_status import TaskStatus


def create_task(
    *,
    store: TaskStore,
    executor: TaskExecutor,
    tool_name: str,
    arguments: dict[str, Any],
    token: TokenInfo,
    ttl_ms: int | None,
    poll_interval_ms: int | None,
    can_elicit: bool = False,
) -> Task:
    """Persist a task, hand it to the executor, and return the client's handle."""
    created: str = now_iso()
    seed = Task(
        task_id=new_task_id(),
        status=TaskStatus.WORKING,
        created_at=created,
        last_updated_at=created,
        ttl_ms=ttl_ms,
        poll_interval_ms=poll_interval_ms,
    )
    record = TaskRecord(
        task=seed,
        tool_name=tool_name,
        arguments=arguments,
        principal_id=principal_for_token(token),
        user_pk=getattr(token.user, "pk", None),
        scopes=tuple(token.scopes),
        audience=token.audience,
        can_elicit=can_elicit,
    )
    store.create(record)

    try:
        executor.enqueue(record.task_id)
    except Exception as exc:  # noqa: BLE001 — any broker failure, reported as one
        failed: TaskRecord = record.with_task(
            status=TaskStatus.FAILED,
            last_updated_at=now_iso(),
            status_message=f"The task could not be queued for execution: {exc}",
        )
        store.save(failed)
        return failed.to_wire()

    store.save(replace(record, enqueued=True))
    return seed


__all__ = ["create_task"]
