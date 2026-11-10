from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django_spec_mcp.types.task_status import TaskStatus


@dataclass(frozen=True)
class Task:
    """The wire shape of a task, in every message that carries one."""

    task_id: str
    status: TaskStatus
    created_at: str
    last_updated_at: str
    ttl_ms: int | None = None
    poll_interval_ms: int | None = None
    status_message: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    input_requests: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Project to the wire, carrying only the field this status licenses."""
        out: dict[str, Any] = {
            "taskId": self.task_id,
            "status": self.status.value,
            "createdAt": self.created_at,
            "lastUpdatedAt": self.last_updated_at,
            "ttlMs": self.ttl_ms,
        }
        if self.poll_interval_ms is not None:
            out["pollIntervalMs"] = self.poll_interval_ms
        if self.status_message is not None:
            out["statusMessage"] = self.status_message
        if self.status is TaskStatus.COMPLETED:
            out["result"] = self.result if self.result is not None else {}
        elif self.status is TaskStatus.FAILED:
            out["error"] = self.error if self.error is not None else {}
        elif self.status is TaskStatus.INPUT_REQUIRED:
            out["inputRequests"] = self.input_requests if self.input_requests is not None else {}
        return out


__all__ = ["Task"]
