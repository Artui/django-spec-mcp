from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from asgiref.sync import sync_to_async

from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.subscriptions.publish_invalidations import publish_invalidations
from django_spec_mcp.subscriptions.render_invalidations import render_invalidations
from django_spec_mcp.types.result_type import ResultType


def announce_invalidations(
    binding: Any,
    result: Any,
    arguments: Mapping[str, Any],
    context: MCPCallContext,
) -> None:
    """Publish a binding's ``invalidates=`` URIs for a call that changed something."""
    uris = _uris(binding, result, arguments)
    publish_invalidations(context.subscriptions, uris)


async def announce_invalidations_async(
    binding: Any,
    result: Any,
    arguments: Mapping[str, Any],
    context: MCPCallContext,
) -> None:
    """The async transport's route to the same function."""
    await sync_to_async(announce_invalidations, thread_sensitive=True)(
        binding, result, arguments, context
    )


def _uris(binding: Any, result: Any, arguments: Mapping[str, Any]) -> tuple[str, ...]:
    """The URIs to announce, or nothing at all."""
    templates: tuple[str, ...] = getattr(binding, "invalidates", ())
    if (
        not templates
        or not isinstance(result, dict)
        or result.get("isError")
        or result.get("resultType", ResultType.COMPLETE.value) != ResultType.COMPLETE.value
    ):
        return ()
    return render_invalidations(templates, payload=result, arguments=arguments)


__all__ = ["announce_invalidations", "announce_invalidations_async"]
