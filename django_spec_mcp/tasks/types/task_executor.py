from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TaskExecutor(Protocol):
    """Where a created task goes to be worked on."""

    def enqueue(self, task_id: str) -> None: ...


__all__ = ["TaskExecutor"]
