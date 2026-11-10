from __future__ import annotations

from typing import Any

from django_spec_mcp.handlers.handle_initialize import build_capabilities
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import catalog_cache_hints
from django_spec_mcp.protocol.build_server_info import build_server_info
from django_spec_mcp.protocol.types.discover_result import DiscoverResult
from django_spec_mcp.protocol.types.implementation import Implementation
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.types.modern_protocol_versions import MODERN_PROTOCOL_VERSIONS


def handle_server_discover(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Report this server's protocol versions, capabilities and identity."""
    server_info: Implementation | None = context.server_info
    if server_info is None:
        server_info = build_server_info()
    result: dict[str, Any] = DiscoverResult(
        supported_versions=context.config.protocol_versions,
        capabilities=build_capabilities(
            context, modern=context.protocol_version in MODERN_PROTOCOL_VERSIONS
        ),
        server_info=server_info,
        instructions=context.instructions,
    ).to_dict()
    return {
        **result,
        **catalog_cache_hints(ttl_ms=context.config.catalog_cache_ttl_ms, per_caller=False),
    }


__all__ = ["handle_server_discover"]
