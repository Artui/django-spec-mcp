from __future__ import annotations

from typing import Any

from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.build_server_info import build_server_info
from django_spec_mcp.protocol.types.implementation import Implementation
from django_spec_mcp.protocol.types.initialize_params import InitializeParams
from django_spec_mcp.protocol.types.initialize_result import InitializeResult
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.server_capabilities import ServerCapabilities
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.tasks_extension_id import TASKS_EXTENSION_ID


def handle_initialize(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> InitializeResult | JsonRpcError:
    """Handle the MCP ``initialize`` request."""
    if not isinstance(params, dict):
        return JsonRpcError(
            code=JsonRpcErrorCode.INVALID_PARAMS,
            message="initialize params must be an object",
        )

    parsed: InitializeParams = InitializeParams.from_payload(params)
    supported: tuple[str, ...] = context.config.legacy_protocol_versions
    if not supported:
        return JsonRpcError(
            code=JsonRpcErrorCode.INVALID_PARAMS,
            message=(
                "This server no longer serves the initialize handshake. It supports "
                f"{', '.join(context.config.protocol_versions)}, which carry per-request "
                "metadata instead of negotiating. Send server/discover."
            ),
        )
    chosen: str = parsed.protocol_version if parsed.protocol_version in supported else supported[0]

    server_info: Implementation | None = context.server_info
    if server_info is None:
        server_info = build_server_info()
    return InitializeResult(
        protocol_version=chosen,
        capabilities=build_capabilities(context, modern=False),
        server_info=server_info,
        instructions=context.instructions,
    )


def build_capabilities(context: MCPCallContext, *, modern: bool) -> ServerCapabilities:
    """What this server can answer, from its registries."""
    extensions: dict[str, Any] = {}
    if modern and context.tasks is not None and context.task_executor is not None:
        extensions[TASKS_EXTENSION_ID] = {}

    pushes: bool = modern and context.subscriptions is not None
    return ServerCapabilities(
        tools=_capability(len(context.tools) > 0, listChanged=pushes),
        resources=_capability(len(context.resources) > 0, listChanged=pushes, subscribe=pushes),
        prompts=_capability(len(context.prompts) > 0, listChanged=pushes),
        completions={} if _has_completers(context) else None,
        extensions=extensions or None,
    )


def _capability(present: bool, **flags: bool) -> dict[str, Any] | None:
    """A capability object, or ``None`` when there is nothing behind it."""
    if not present:
        return None
    return {name: True for name, value in flags.items() if value}


def _has_completers(context: MCPCallContext) -> bool:
    """Whether any registered prompt or resource can complete an argument."""
    return any(b.completions for b in context.prompts.all()) or any(
        b.completions for b in context.resources.all()
    )


__all__ = ["build_capabilities", "handle_initialize"]
