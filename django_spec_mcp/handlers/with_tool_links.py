from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django_service_specs import SelectorSpec, ServiceSpec, row_links

from django_spec_mcp.registry.tool_registry import ToolRegistry
from django_spec_mcp.types.affordances_key import AFFORDANCES_KEY


def with_tool_links(spec: ServiceSpec | SelectorSpec, presented: Any, tools: ToolRegistry) -> Any:
    """``presented`` with each available link's ``tool`` and ``arguments`` in its entry."""
    named: dict[str, tuple[str, frozenset[str]] | None] = {}
    if isinstance(presented, list | tuple):
        return [_linked(spec, row, tools, named) for row in presented]
    return _linked(spec, presented, tools, named)


def _linked(
    spec: ServiceSpec | SelectorSpec,
    row: Any,
    tools: ToolRegistry,
    named: dict[str, tuple[str, frozenset[str]] | None],
) -> Any:
    if not isinstance(row, Mapping) or AFFORDANCES_KEY not in row:
        return row
    entries = dict(row[AFFORDANCES_KEY])
    for name, link in row_links(spec, row).items():
        if not link["available"]:
            continue
        if name not in named:
            binding = tools.binding_of(link["spec"])
            named[name] = (
                None if binding is None else (binding.name, binding.server_arguments.names())
            )
        target = named[name]
        if target is None:
            continue
        tool, filled = target
        arguments = {read: value for read, value in link["arguments"].items() if read not in filled}
        entries[name] = {**entries[name], "tool": tool, "arguments": arguments}
    return {**row, AFFORDANCES_KEY: entries}


__all__ = ["with_tool_links"]
