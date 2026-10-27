"""``TASK_STATUS_METHOD`` - pushed when a task changes status, to whoever subscribed to that task."""

from __future__ import annotations

TASK_STATUS_METHOD: str = "notifications/tasks"
"""Pushed when a task changes status, to whoever subscribed to that task.

Carries the **whole** task, not a delta — identical to what ``tasks/get`` would
have returned at that moment — so a missed notification costs nothing and
polling stays genuinely optional."""
