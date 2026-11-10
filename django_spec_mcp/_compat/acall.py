from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from asgiref.sync import sync_to_async


async def acall(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoke ``fn`` from async code regardless of whether it's async or sync."""
    if inspect.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    return await sync_to_async(fn)(*args, **kwargs)


__all__ = ["acall"]
