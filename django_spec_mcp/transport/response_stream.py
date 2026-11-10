from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.http import StreamingHttpResponse
from django_service_specs import ProgressReporter

from django_spec_mcp.observability.get_logger import get_logger
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.json_rpc_response import JsonRpcResponse
from django_spec_mcp.transport.sse_response import format_event, keepalive_interval_seconds
from django_spec_mcp.types.internal_error_message import INTERNAL_ERROR_MESSAGE
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.jsonrpc_version import JSONRPC_VERSION

_PROGRESS_METHOD: str = "notifications/progress"

logger = get_logger(__name__)


@dataclass(frozen=True)
class _Finished:
    """Queue sentinel carrying the dispatch's final JSON-RPC envelope."""

    body: dict[str, Any]


def build_response_stream(
    *,
    dispatch: Callable[[ProgressReporter], Awaitable[Any]],
    request_id: Any,
    progress_token: str | int,
    max_notifications: int,
    keepalive: float | None = None,
) -> StreamingHttpResponse:
    """Answer one POST with an SSE stream carrying progress, then the response."""
    response = StreamingHttpResponse(
        _stream(
            dispatch=dispatch,
            request_id=request_id,
            progress_token=progress_token,
            max_notifications=max_notifications,
            keepalive=keepalive if keepalive is not None else keepalive_interval_seconds(),
        ),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


async def _stream(
    *,
    dispatch: Callable[[ProgressReporter], Awaitable[Any]],
    request_id: Any,
    progress_token: str | int,
    max_notifications: int,
    keepalive: float,
) -> AsyncIterator[bytes]:
    """Drain progress frames until the dispatch finishes, then send its result."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    reporter = _StreamReporter(
        loop=loop,
        queue=queue,
        token=progress_token,
        remaining=max_notifications,
    )
    task: asyncio.Task[None] = asyncio.ensure_future(
        _run(dispatch(reporter), queue, request_id=request_id)
    )
    try:
        while True:
            try:
                item: Any = await asyncio.wait_for(queue.get(), timeout=keepalive)
            except asyncio.TimeoutError:  # noqa: UP041 — 3.10 keeps this distinct
                yield b": keepalive\n\n"
                continue
            if isinstance(item, _Finished):
                yield format_event(item.body)
                return
            yield format_event(item)
    finally:
        if not task.done():
            task.cancel()


async def _run(coro: Awaitable[Any], queue: asyncio.Queue[Any], *, request_id: Any) -> None:
    """Await the dispatch and put its final envelope on the queue."""
    try:
        result: Any = await coro
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — see the docstring
        logger.exception("Unhandled exception in a streamed dispatch (request id %r)", request_id)
        body = JsonRpcResponse(
            id=request_id,
            error=JsonRpcError(JsonRpcErrorCode.INTERNAL_ERROR, INTERNAL_ERROR_MESSAGE),
        ).to_dict()
    else:
        body = (
            JsonRpcResponse(id=request_id, error=result).to_dict()
            if isinstance(result, JsonRpcError)
            else JsonRpcResponse(id=request_id, result=result).to_dict()
        )
    asyncio.get_running_loop().call_soon(queue.put_nowait, _Finished(body))


class _StreamReporter:
    """The ``ProgressReporter`` handed to the dispatched callable."""

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[Any],
        token: str | int,
        remaining: int,
    ) -> None:
        self._loop = loop
        self._queue = queue
        self._token = token
        self._remaining = remaining
        self._last: float | None = None
        self._lock = threading.Lock()

    def __call__(
        self,
        progress: float,
        *,
        total: float | None = None,
        message: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        with self._lock:
            # Both decisions and both writes sit in one critical section.
            if self._remaining <= 0:
                return
            if self._last is not None and progress <= self._last:
                return
            self._last = progress
            self._remaining -= 1
        params: dict[str, Any] = {"progressToken": self._token, "progress": progress}
        if total is not None:
            params["total"] = total
        if message is not None:
            params["message"] = message
        if meta:
            params["_meta"] = dict(meta)
        frame: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "method": _PROGRESS_METHOD,
            "params": params,
        }
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, frame)
        except RuntimeError:
            return


__all__ = ["build_response_stream"]
