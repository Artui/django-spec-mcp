from __future__ import annotations

from typing import Protocol, runtime_checkable

from django_spec_mcp.tasks.types.task_record import TaskRecord


@runtime_checkable
class TaskStore(Protocol):
    """Pluggable persistence for tasks, mirroring ``SessionStore``."""

    def create(self, record: TaskRecord) -> None: ...

    def get(self, task_id: str) -> TaskRecord | None: ...

    def save(self, record: TaskRecord) -> None: ...

    def delete(self, task_id: str) -> None: ...


__all__ = ["TaskStore"]
