from __future__ import annotations

from django_spec_mcp.tasks.types.task_record import TaskRecord


class InMemoryTaskStore:
    """Task store held in one process's memory."""

    def __init__(self) -> None:
        # Per instance, never module-level: two servers keep separate task spaces.
        self._records: dict[str, TaskRecord] = {}

    def create(self, record: TaskRecord) -> None:
        self._records[record.task_id] = record

    def get(self, task_id: str) -> TaskRecord | None:
        return self._records.get(task_id)

    def save(self, record: TaskRecord) -> None:
        self._records[record.task_id] = record

    def delete(self, task_id: str) -> None:
        self._records.pop(task_id, None)


__all__ = ["InMemoryTaskStore"]
