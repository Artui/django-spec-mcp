"""``ToolKind``: the two kinds of tool a binding is."""

from __future__ import annotations

from django_spec_mcp.types.tool_kind import ToolKind


def test_tool_kind_has_two_members() -> None:
    assert {m.name for m in ToolKind} == {"SERVICE", "SELECTOR"}


def test_tool_kind_str_value_is_internal_only() -> None:
    """Plain :class:`Enum` — members don't compare equal to their string values."""
    assert ToolKind.SERVICE != "service"
    assert ToolKind.SELECTOR.value == "selector"
