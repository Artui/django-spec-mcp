from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from django_service_specs import ProgressReporter

from django_spec_mcp.tasks.types.task_record import TaskRecord
from django_spec_mcp.tasks.types.task_store import TaskStore
from django_spec_mcp.tasks.utils import now_iso


def report_task_progress(store: TaskStore, task_id: str) -> ProgressReporter:
    """A ``ProgressReporter`` that writes onto the task record."""

    def report(
        progress: float,
        *,
        total: float | None = None,
        message: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        del meta  # No slot on the wire, so no place to put it.
        try:
            record: TaskRecord | None = store.get(task_id)
            if record is None or record.status.is_terminal:
                return
            updated: TaskRecord = replace(
                record.with_task(
                    status_message=_render_progress(progress, total, message),
                    last_updated_at=now_iso(),
                ),
                progress=progress,
                total=total,
            )
            store.save(updated)
        except Exception:  # noqa: BLE001 — a telemetry write must not fail the work.
            return

    return report


def _render_progress(progress: float, total: float | None, message: str | None) -> str:
    """The ``statusMessage`` a polling client actually sees."""
    count: str = f"{progress:g}" if total is None else f"{progress:g}/{total:g}"
    return count if message is None else f"{message} ({count})"


__all__ = ["report_task_progress"]
