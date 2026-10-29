from __future__ import annotations

from typing import Any

from django_spec_mcp.handlers.is_binding_listable import is_binding_listable
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import catalog_cursor, catalog_page
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.resource_template import ResourceTemplate
from django_spec_mcp.registry.types.resource_binding import ResourceBinding


def handle_resources_templates_list(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """List the parameterised resource templates the server exposes, paginated."""
    cursor = catalog_cursor(params)
    if isinstance(cursor, JsonRpcError):
        return cursor

    bindings = list(context.resources.templates())
    if context.config.filter_listings_by_permissions:
        bindings = [
            b
            for b in bindings
            if is_binding_listable(
                b,
                context.http_request,
                context.token,
                config=context.config,
                pool_seeds=context.pool_seeds,
            )
        ]

    return catalog_page(
        "resourceTemplates",
        bindings,
        cursor,
        _template_entry,
        context,
        per_caller=context.config.filter_listings_by_permissions,
    )


def _template_entry(binding: ResourceBinding[Any]) -> dict[str, Any]:
    return ResourceTemplate(
        uri_template=binding.uri_template,
        name=binding.name,
        description=binding.description,
        title=binding.title,
        icons=binding.icons,
        mime_type=binding.mime_type,
        annotations=dict(binding.annotations) or None,
        meta=dict(binding.meta) or None,
    ).to_dict()


__all__ = ["handle_resources_templates_list"]
