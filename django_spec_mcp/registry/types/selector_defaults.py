from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django_service_specs import UnknownArguments
from rest_framework_services import ArgumentBinding

from django_spec_mcp.types.output_format import OutputFormat


@dataclass(frozen=True)
class SelectorDefaults:
    """Per-kind defaults for
    [`register_tools`][django_spec_mcp.registry.register_tools.register_tools] over
    selector definitions.

    Sister of
    [`ServiceDefaults`][django_spec_mcp.registry.types.service_defaults.ServiceDefaults],
    with the same convention: ``None`` is "no override", deferring to the per-definition
    value or to
    [`MCPServer.register_selector_tool`][django_spec_mcp.server.mcp_server.MCPServer.register_selector_tool]'s
    own default, and a per-definition kwarg always wins on conflict.

    The selector-only knobs live here too, so a project wanting every selector
    tool to paginate by default says so once. Filtering is not among them —
    ``filter_set`` is declared on each ``SelectorSpec``, never as a
    registration default, and it carries ordering with it.
    """

    description: str | None = None
    title: str | None = None
    input_serializer: type | None = None
    output_format: OutputFormat | None = None
    permissions: Sequence[Any] | None = None
    rate_limits: Sequence[Any] | None = None
    annotations: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    paginate: bool | None = None
    include_structured_content: bool | None = None
    include_output_schema: bool | None = None
    argument_binding: ArgumentBinding | None = None
    unknown_arguments: UnknownArguments | None = None


__all__ = ["SelectorDefaults"]
