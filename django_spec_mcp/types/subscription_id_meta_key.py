"""``SUBSCRIPTION_ID_META_KEY`` - _meta key correlating a notification with the subscription that wanted it."""

from __future__ import annotations

SUBSCRIPTION_ID_META_KEY: str = "io.modelcontextprotocol/subscriptionId"
"""``_meta`` key correlating a notification with the subscription that wanted it.

The value is the JSON-RPC id of the ``subscriptions/listen`` request that
opened the stream, carried on **every** notification it delivers and on the
result that closes it, so a client can run several subscriptions over one
connection and still tell them apart.
"""
