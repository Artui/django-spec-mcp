"""``ToolRegistry`` holds kernel bindings by name, in registration order.

The drf-services original asked the same of a ``ToolBinding`` over a drf-services
spec and is kept beside this file as ``test_tool_registry_drfs.py``.
"""

from __future__ import annotations

import pytest
from django_service_specs import ServiceSpec, Unrestricted

from django_spec_mcp.registry.tool_registry import ToolRegistry
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from tests.testapp.services import record_invoice


def _binding(name: str = "t") -> KernelToolBinding:
    return KernelToolBinding(
        name=name,
        description=None,
        spec=ServiceSpec(service=record_invoice, permissions=[Unrestricted()]),
    )


def test_register_and_lookup() -> None:
    reg = ToolRegistry()
    a, b = _binding("b-first"), _binding("a-second")
    reg.register(a)
    reg.register(b)
    assert reg.get("b-first") is a
    assert "a-second" in reg
    assert reg.get("missing") is None
    assert len(reg) == 2
    # Authored order, not sorted by name.
    assert reg.all() == [a, b]


def test_register_duplicate_raises() -> None:
    reg = ToolRegistry()
    reg.register(_binding("a"))
    with pytest.raises(ValueError, match="Duplicate MCP tool name: 'a'"):
        reg.register(_binding("a"))
