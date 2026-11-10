from __future__ import annotations

from typing import Any

from asgiref.sync import async_to_sync
from django.db import transaction

from django_spec_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from django_spec_mcp.subscriptions.utils import topic_for_resource
from django_spec_mcp.types.jsonrpc_version import JSONRPC_VERSION
from django_spec_mcp.types.resource_updated_method import RESOURCE_UPDATED_METHOD


def publish_invalidations(broker: SubscriptionBroker | None, uris: tuple[str, ...]) -> None:
    """Announce that ``uris`` changed — **after the transaction commits**."""
    if broker is None or not uris:
        return
    transaction.on_commit(lambda: _publish_now(broker, uris))


def publish_after_commit(
    broker: SubscriptionBroker | None, topic: str, payload: dict[str, Any]
) -> None:
    """One notification, on the same terms as an invalidation."""
    if broker is None:
        return
    transaction.on_commit(lambda: _publish_one(broker, topic, payload))


def _publish_one(broker: SubscriptionBroker, topic: str, payload: dict[str, Any]) -> None:
    async_to_sync(broker.publish)(topic, payload)


def resource_updated(uri: str) -> dict[str, Any]:
    """The ``notifications/resources/updated`` frame for one URI."""
    return {
        "jsonrpc": JSONRPC_VERSION,
        "method": RESOURCE_UPDATED_METHOD,
        "params": {"uri": uri},
    }


def _publish_now(broker: SubscriptionBroker, uris: tuple[str, ...]) -> None:
    """Run the publishes from a committed, loop-free thread."""
    async_to_sync(_publish_all)(broker, uris)


async def _publish_all(broker: SubscriptionBroker, uris: tuple[str, ...]) -> None:
    for uri in uris:
        await broker.publish(topic_for_resource(uri), resource_updated(uri))


__all__ = ["publish_after_commit", "publish_invalidations", "resource_updated"]
