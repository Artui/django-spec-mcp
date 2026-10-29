from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from asgiref.sync import sync_to_async


async def acall(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoke ``fn`` from async code regardless of whether it's async or sync.

    Async callables are awaited directly; sync ones are dispatched to a thread
    via ``asgiref.sync.sync_to_async`` so they don't block the event loop.
    The bridge the async transport uses for collaborators (the auth backend,
    the session store) that are not required to be async-native.

    **This bridges the callable it is handed, not the callables that one goes on
    to invoke.** A prompt's or a view's checks and every rate limit are reached
    through the aggregate helpers ``judge_binding`` / ``consume_rate_limits``,
    which are ordinary sync functions — so ``acall(judge_binding, …)`` takes the
    thread hop and an awaitable a ``has_permission`` hands back inside it is
    still never awaited, which would fail open. Those hooks are **synchronous by contract on both
    transports** and are guarded by
    ``django_spec_mcp._compat.reject_awaitable.reject_awaitable`` rather
    than bridged here.
    """
    if inspect.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    return await sync_to_async(fn)(*args, **kwargs)


__all__ = ["acall"]
