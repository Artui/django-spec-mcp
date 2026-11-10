from __future__ import annotations

from django_spec_mcp.subscriptions.publish_invalidations import publish_after_commit
from django_spec_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from django_spec_mcp.subscriptions.utils import topic_for_task
from django_spec_mcp.tasks.types.task_record import TaskRecord
from django_spec_mcp.types.jsonrpc_version import JSONRPC_VERSION
from django_spec_mcp.types.task_status_method import TASK_STATUS_METHOD


def publish_task_status(broker: SubscriptionBroker | None, record: TaskRecord) -> None:
    """Announce a task's new status to whoever subscribed to that task."""
    publish_after_commit(
        broker,
        topic_for_task(record.task_id),
        {
            "jsonrpc": JSONRPC_VERSION,
            "method": TASK_STATUS_METHOD,
            "params": record.to_wire().to_dict(),
        },
    )


__all__: list[str] = ["publish_task_status"]
