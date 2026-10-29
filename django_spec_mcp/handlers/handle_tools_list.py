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
    """Return the catalog of tools the server exposes, paginated.

    JSON Schemas are rebuilt on every request rather than cached on the
    binding — discovery already runs at router-construction time, so the
    relative cost is small and it keeps bindings cheap to construct. It is also
    what lets a binding stay server-neutral: the wording a listing carries (the
    handle description and line, a paged tool's scope sentence) is the context's
    ``conventions``, read and rendered here, so the server that owns the request
    decides it and the schema builders write it as given.

    Pagination is opaque-cursor per the MCP spec: clients pass back the
    ``nextCursor`` they received without inspecting it.

    A tool that an operation-scope affordance refuses right now is left out,
    and is still callable by name: ``tools/call`` never consults the listing,
    so a client holding an older one gets the refusal and its ``code``, not an
    unknown tool. See ``offered_tools``.

    ``include_unavailable=True`` skips that question and lists every tool the
    caller may see. The wire never passes it: it is for ``MCPServer.list_tools``,
    whose in-process consumer builds its tool definitions once and asks
    ``unavailable_tools`` each step, and so needs a definition for a tool that
    is unavailable when it lists and becomes available later.
    """
    cursor = catalog_cursor(params)
    if isinstance(cursor, JsonRpcError):
        return cursor

    # Filtered before paginating, so ``nextCursor`` reflects the visible slice
    # rather than the full registry.
    bindings = listable_tool_bindings(context)
    # Availability runs whatever the permissions flag says, and after it, so a
    # tool this caller may not see is never asked about. Also before paginating,
    # for the same reason as above: a cursor over a slice the client never sees
    # would hand back a short or empty page. Nothing is asked when the caller
    # asked for every tool, so such a listing's ``cacheScope`` owes nothing to
    # an answer about this caller.
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
