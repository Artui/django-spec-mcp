from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from django.http import StreamingHttpResponse

from django_spec_mcp.protocol.types.json_rpc_response import JsonRpcResponse
from django_spec_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from django_spec_mcp.subscriptions.types.subscription_filter import SubscriptionFilter
from django_spec_mcp.transport.sse_response import format_event, keepalive_interval_seconds
from django_spec_mcp.types.jsonrpc_version import JSONRPC_VERSION
from django_spec_mcp.types.subscription_id_meta_key import SUBSCRIPTION_ID_META_KEY
from django_spec_mcp.types.subscriptions_acknowledged_method import (
    SUBSCRIPTIONS_ACKNOWLEDGED_METHOD,
)


def build_subscription_stream(
    *,
    broker: SubscriptionBroker,
    topics: frozenset[str],
    granted: SubscriptionFilter,
    request_id: Any,
    max_seconds: float | None,
    keepalive: float | None = None,
) -> StreamingHttpResponse:
    """Answer ``subscriptions/listen`` with a stream that stays open."""
    return _sse(
        _stream(
            broker=broker,
            topics=topics,
            granted=granted,
            request_id=request_id,
            max_seconds=max_seconds,
            keepalive=keepalive if keepalive is not None else keepalive_interval_seconds(),
        )
    )


def _sse(body: AsyncIterator[bytes]) -> StreamingHttpResponse:
    response = StreamingHttpResponse(body, content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    # Without this nginx buffers the response, and a stream that never ends never flushes.
    response["X-Accel-Buffering"] = "no"
    return response


async def _stream(
    *,
    broker: SubscriptionBroker,
    topics: frozenset[str],
    granted: SubscriptionFilter,
    request_id: Any,
    max_seconds: float | None,
    keepalive: float,
) -> AsyncIterator[bytes]:
    if not topics:
        # Nothing was granted, so nothing can ever arrive.
        yield format_event(_acknowledgement(granted, request_id))
        yield format_event(subscription_closed_response(request_id))
        return

    queue: asyncio.Queue[Any] = await broker.subscribe(topics)
    deadline: float | None = (
        None if max_seconds is None else asyncio.get_running_loop().time() + max_seconds
    )
    try:
        yield format_event(_acknowledgement(granted, request_id))
        while True:
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                yield format_event(subscription_closed_response(request_id))
                return
            try:
                payload: Any = await asyncio.wait_for(queue.get(), timeout=keepalive)
            except asyncio.TimeoutError:  # noqa: UP041 — 3.10 keeps this distinct
                yield b": keepalive\n\n"
                continue
            yield format_event(_with_subscription_id(payload, request_id))
    finally:
        broker.unsubscribe(queue)


def _acknowledgement(granted: SubscriptionFilter, request_id: Any) -> dict[str, Any]:
    """The mandatory first frame, naming what will actually be delivered."""
    return {
        "jsonrpc": JSONRPC_VERSION,
        "method": SUBSCRIPTIONS_ACKNOWLEDGED_METHOD,
        "params": {
            "notifications": granted.to_dict(),
            "_meta": {SUBSCRIPTION_ID_META_KEY: request_id},
        },
    }


def _with_subscription_id(payload: Any, request_id: Any) -> Any:
    """Stamp the subscription id into a notification's ``params._meta``."""
    if not isinstance(payload, dict):
        return payload
    params: Any = payload.get("params")
    params = dict(params) if isinstance(params, dict) else {}
    meta: Any = params.get("_meta")
    meta = dict(meta) if isinstance(meta, dict) else {}
    meta.setdefault(SUBSCRIPTION_ID_META_KEY, request_id)
    params["_meta"] = meta
    return {**payload, "params": params}


def subscription_closed_response(request_id: Any) -> dict[str, Any]:
    """The result that ends a subscription the *server* tore down."""
    return JsonRpcResponse(
        id=request_id,
        result={"_meta": {SUBSCRIPTION_ID_META_KEY: request_id}},
    ).to_dict()


__all__ = ["build_subscription_stream", "subscription_closed_response"]
