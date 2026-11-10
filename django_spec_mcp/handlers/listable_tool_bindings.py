from __future__ import annotations

from django_spec_mcp.handlers.is_binding_listable import is_binding_listable
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding


def listable_tool_bindings(context: MCPCallContext) -> list[KernelToolBinding]:
    """Every registered tool, less those ``FILTER_LISTINGS_BY_PERMISSIONS`` hides."""
    bindings: list[KernelToolBinding] = list(context.tools.all())
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
    return bindings


__all__ = ["listable_tool_bindings"]
