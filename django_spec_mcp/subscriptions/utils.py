from __future__ import annotations

from django_spec_mcp.types.notification_kind import NotificationKind


def topic_for_kind(kind: NotificationKind) -> str:
    """The topic a list-changed notification is published to."""
    return f"kind:{kind.value}"


def topic_for_resource(uri: str) -> str:
    """The topic ``notifications/resources/updated`` for ``uri`` goes to.

    **Exact URI, never a prefix.** The spec permits notifying about a
    *sub*-resource of the one subscribed to, which invites prefix matching, but
    a prefix match over a free-form URI guesses at a scheme this package does
    not own and fails both ways: ``invoices://1`` would match ``invoices://11``,
    while a tenant-scoped ``t1/invoices://…`` would match nothing.

    A publisher that wants a collection watched says so, by publishing the
    collection URI alongside the instance one — an ``invalidates=`` naming both
    ``invoices://{pk}`` and ``invoices://`` is explicit and reviewable where a
    matching rule is neither.
    """
    return f"resource:{uri}"


def topic_for_task(task_id: str) -> str:
    """The topic a task's ``notifications/tasks`` frames go to."""
    return f"task:{task_id}"


__all__ = ["topic_for_kind", "topic_for_resource", "topic_for_task"]
