from __future__ import annotations

import inspect
from typing import Any

from django.core.exceptions import ImproperlyConfigured


def reject_awaitable(result: Any, *, call: str, remedy: str, hazard: str) -> Any:
    """Return ``result``, or refuse it when nothing on this path will await it."""
    if not inspect.isawaitable(result):
        return result
    if inspect.iscoroutine(result):
        result.close()
    raise ImproperlyConfigured(
        f"{call} returned an awaitable, but nothing on this path awaits it. "
        f"{remedy} Refusing the request: {hazard}"
    )


__all__ = ["reject_awaitable"]
