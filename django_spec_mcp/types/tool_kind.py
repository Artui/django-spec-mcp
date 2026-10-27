"""``ToolKind`` - discriminator for [`ToolDefinition`][django_spec_mcp.registry.types.tool_definition.ToolDefinition] and the [`register_tools`][django_spec_mcp.registry.register_tools.register_tools] dispatch table."""

from __future__ import annotations

from enum import Enum


class ToolKind(Enum):
    """Discriminator for
    [`ToolDefinition`][django_spec_mcp.registry.types.tool_definition.ToolDefinition]
    and the
    [`register_tools`][django_spec_mcp.registry.register_tools.register_tools]
    dispatch table.

    Internal-only — never appears on the wire. ``SERVICE`` maps to
    [`MCPServer.register_service_tool`][django_spec_mcp.server.mcp_server.MCPServer.register_service_tool],
    ``SELECTOR`` to
    [`MCPServer.register_selector_tool`][django_spec_mcp.server.mcp_server.MCPServer.register_selector_tool].
    Prefer
    [`ToolDefinition.service`][django_spec_mcp.registry.types.tool_definition.ToolDefinition.service]
    /
    [`ToolDefinition.selector`][django_spec_mcp.registry.types.tool_definition.ToolDefinition.selector]
    over passing this kwarg by hand — those are the typed entry points."""

    SERVICE = "service"
    SELECTOR = "selector"
