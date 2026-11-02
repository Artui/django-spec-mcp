from __future__ import annotations

import hashlib
import importlib
from collections.abc import Callable
from typing import Any

from django_service_specs import arun_selector, is_async, run_selector

from django_spec_mcp._compat.acall import acall
from django_spec_mcp.types.namespace_digest_chars import NAMESPACE_DIGEST_CHARS

# The kernel's `arun_selector` runs sync callables inline, so any ORM call
# inside them raises `SynchronousOnlyOperation` under an event loop. This routes
# the sync branch through `sync_to_async` instead, keeping native async dispatch
# for genuinely async selectors.


async def arun_selector_sync_safe(fn: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    if is_async(fn):
        return await arun_selector(fn, kwargs)
    return await acall(run_selector, fn, kwargs)


def namespace_digest(namespace: str) -> str:
    """Reduce a free-form namespace (a server name) to a token a cache key, Redis
    key or channel name can hold.

    Not for secrecy - a server name is public. It only guarantees a well-formed
    key on every backend.
    """
    return hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:NAMESPACE_DIGEST_CHARS]


def resolve_async_redis() -> Any:
    """Resolve ``redis.asyncio.Redis`` without making it an import-time dependency.

    ``redis`` is an optional extra, so importing a Redis-backed module without it
    must not crash the package; the ``ImportError`` fires only when a consumer
    constructs the store. Resolved through ``importlib`` so the binding stays
    plain ``Any`` and the type checker cannot narrow it to the imported class.
    """
    try:
        return importlib.import_module("redis.asyncio").Redis
    except ImportError:  # pragma: no cover - exercised by the no-extras smoke job
        return None


__all__ = ["arun_selector_sync_safe", "namespace_digest", "resolve_async_redis"]
