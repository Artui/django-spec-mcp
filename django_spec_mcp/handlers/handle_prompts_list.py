from __future__ import annotations

from typing import Any

from django_spec_mcp.handlers.is_binding_listable import is_binding_listable
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import catalog_cursor, catalog_page
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.prompt import Prompt
from django_spec_mcp.registry.types.prompt_binding import PromptBinding


def handle_prompts_list(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Return the catalog of prompts the server exposes, paginated.

    Mirrors ``tools/list`` / ``resources/list`` — opaque cursor in,
    ``nextCursor`` out when the page doesn't reach the end.
    """
    cursor = catalog_cursor(params)
    if isinstance(cursor, JsonRpcError):
        return cursor

    bindings = list(context.prompts.all())
    if context.config.filter_listings_by_permissions:
        bindings = [
            b for b in bindings if is_binding_listable(b, context.http_request, context.token)
        ]

    return catalog_page(
        "prompts",
        bindings,
        cursor,
        _prompt_entry,
        context,
        per_caller=context.config.filter_listings_by_permissions,
    )


def _prompt_entry(binding: PromptBinding) -> dict[str, Any]:
    return Prompt(
        name=binding.name,
        description=binding.description,
        title=binding.title,
        icons=binding.icons,
        arguments=list(binding.arguments),
        annotations=dict(binding.annotations) or None,
        meta=dict(binding.meta) or None,
    ).to_dict()


__all__ = ["handle_prompts_list"]
