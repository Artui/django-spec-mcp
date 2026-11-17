from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django_service_specs import SelectorSpec, ServiceSpec, link_bindings, row_links

from django_spec_mcp.observability import get_logger
from django_spec_mcp.registry.tool_registry import ToolRegistry
from django_spec_mcp.types.affordances_key import AFFORDANCES_KEY

logger = get_logger(__name__)


def with_tool_links(spec: ServiceSpec | SelectorSpec, presented: Any, tools: ToolRegistry) -> Any:
    """``presented`` with each available link's ``tool`` and ``arguments`` in its entry."""
    if not link_bindings(spec):
        return presented
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
            named[name] = _tool_of(link["spec"], name, tools)
        target = named[name]
        if target is None:
            continue
        tool, filled = target
        arguments = {read: value for read, value in link["arguments"].items() if read not in filled}
        entries[name] = {**entries[name], "tool": tool, "arguments": arguments}
    return {**row, AFFORDANCES_KEY: entries}


def _tool_of(target: Any, name: str, tools: ToolRegistry) -> tuple[str, frozenset[str]] | None:
    try:
        binding = tools.binding_of(target)
    except ValueError as exc:
        logger.warning("Affordance %r links to no one tool, so it names none: %s", name, exc)
        return None
    return None if binding is None else (binding.name, binding.server_arguments.names())


__all__ = ["with_tool_links"]
