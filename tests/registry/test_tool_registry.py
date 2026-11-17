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


class _Source:
    def __init__(self, spec: ServiceSpec) -> None:
        self.spec = spec

    def as_kernel_spec(self) -> ServiceSpec:
        return self.spec


class TestBindingOf:
    def test_a_spec_under_two_tool_names_has_no_one_name(self) -> None:
        spec = _binding().spec
        reg = ToolRegistry()
        reg.register(KernelToolBinding(name="a", description=None, spec=spec))
        reg.register(KernelToolBinding(name="a_again", description=None, spec=spec))

        with pytest.raises(ValueError, match="a_again"):
            reg.binding_of(spec)

    def test_a_spec_source_is_matched_by_the_spec_it_compiles_to(self) -> None:
        reg = ToolRegistry()
        held = _binding()
        reg.register(held)

        assert reg.binding_of(_Source(held.spec)) is held

    def test_an_equal_spec_is_not_the_one_held(self) -> None:
        reg = ToolRegistry()
        held = _binding()
        reg.register(held)

        assert reg.binding_of(held.spec) is held
        assert reg.binding_of(_binding().spec) is None

    def test_an_empty_registry_holds_nothing(self) -> None:
        assert ToolRegistry().binding_of(_binding().spec) is None
