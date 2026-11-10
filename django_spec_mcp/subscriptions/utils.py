from __future__ import annotations

from django_spec_mcp.types.notification_kind import NotificationKind


def topic_for_kind(kind: NotificationKind) -> str:
    """The topic a list-changed notification is published to."""
    return f"kind:{kind.value}"


def topic_for_resource(uri: str) -> str:
    """The topic ``notifications/resources/updated`` for ``uri`` goes to."""
    return f"resource:{uri}"


def topic_for_task(task_id: str) -> str:
    """The topic a task's ``notifications/tasks`` frames go to."""
    return f"task:{task_id}"


__all__ = ["topic_for_kind", "topic_for_resource", "topic_for_task"]
