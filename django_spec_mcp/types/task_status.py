"""``TaskStatus`` - lifecycle of a task, per the extension."""

from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    """Lifecycle of a task, per the extension.

    ``WORKING`` and ``INPUT_REQUIRED`` are live; the other three are terminal
    and a task never leaves them.

    **``FAILED`` is narrower than it looks.** The spec forbids using the status
    to represent non-JSON-RPC errors. A tool that raises ``ServiceError`` has
    *completed* — it produced a well-formed ``CallToolResult`` carrying
    ``isError: true``. ``FAILED`` is for the task machinery itself failing: the
    worker died, the payload could not be revived. Getting this backwards would
    hide every tool error behind a status the client reads as "the server
    broke".
    """

    WORKING = "working"
    INPUT_REQUIRED = "input_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_TASK_STATUSES


_TERMINAL_TASK_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)
