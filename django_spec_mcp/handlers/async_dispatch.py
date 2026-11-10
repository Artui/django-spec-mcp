from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

from asgiref.sync import sync_to_async

from django_spec_mcp.handlers.dispatch import dispatch
from django_spec_mcp.handlers.handle_completion_complete import handle_completion_complete
from django_spec_mcp.handlers.handle_prompts_get_async import handle_prompts_get_async
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import run_with_deadline
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode


async def adispatch(
    method: str,
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> Any | JsonRpcError:
    """Async sibling of ``dispatch``."""
    if method == "tools/call":
        return await handle_tools_call_async(params, context)
    if method == "resources/read":
        return await _with_deadline(handle_resources_read_async(params, context), context)
    if method == "prompts/get":
        return await _with_deadline(handle_prompts_get_async(params, context), context)
    if method == "completion/complete":
        return await _with_deadline(
            sync_to_async(handle_completion_complete, thread_sensitive=True)(params, context),
            context,
        )
    return await sync_to_async(dispatch, thread_sensitive=True)(method, params, context)


async def _with_deadline(coro: Awaitable[Any], context: MCPCallContext) -> Any:
    """Apply the server's dispatch deadline, rendering expiry as a JSON-RPC error."""
    try:
        return await run_with_deadline(coro, context.config.dispatch_timeout)
    except asyncio.TimeoutError:  # noqa: UP041 — 3.10 keeps this distinct from builtins
        return JsonRpcError(
            JsonRpcErrorCode.SERVER_ERROR,
            "The request exceeded this server's dispatch deadline and was abandoned.",
        )


__all__ = ["adispatch"]
