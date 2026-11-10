from __future__ import annotations

from typing import Any

from django_spec_mcp.handlers.kernel_tool_entry import kernel_tool_entry
from django_spec_mcp.handlers.listable_tool_bindings import listable_tool_bindings
from django_spec_mcp.handlers.offered_tools import offered_tools
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import catalog_cursor, catalog_page
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError


def handle_tools_list(
    params: dict[str, Any] | None,
    context: MCPCallContext,
    *,
    include_unavailable: bool = False,
) -> dict[str, Any] | JsonRpcError:
    """Return the catalog of tools the server exposes, paginated."""
    cursor = catalog_cursor(params)
    if isinstance(cursor, JsonRpcError):
        return cursor

    bindings = listable_tool_bindings(context)
    asked: bool = False
    if not include_unavailable:
        bindings, asked = offered_tools(bindings, context)

    return catalog_page(
        "tools",
        bindings,
        cursor,
        lambda binding: kernel_tool_entry(binding, context),
        context,
        per_caller=context.config.filter_listings_by_permissions or asked,
    )


__all__ = ["handle_tools_list"]
