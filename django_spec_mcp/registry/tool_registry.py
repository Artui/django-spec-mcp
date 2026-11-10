from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import DEFAULT_POOL_SEEDS, PoolSeeds, check_server_arguments

from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding


class ToolRegistry:
    """Name to tool binding lookup."""

    def __init__(self, *, pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS) -> None:
        self._bindings: dict[str, KernelToolBinding] = {}
        self._pool_seeds: PoolSeeds = pool_seeds

    def register(self, binding: KernelToolBinding) -> None:
        """Add ``binding``, refusing a duplicate name."""
        if binding.name in self._bindings:
            raise ValueError(f"Duplicate MCP tool name: {binding.name!r}")
        _refuse_server_arguments_that_cannot_hold(binding, self._pool_seeds)
        self._bindings[binding.name] = binding

    def get(self, name: str) -> KernelToolBinding | None:
        return self._bindings.get(name)

    def all(self) -> list[KernelToolBinding]:
        """Every binding, in **registration order**."""
        return list(self._bindings.values())

    def __len__(self) -> int:
        return len(self._bindings)

    def __contains__(self, name: object) -> bool:
        return name in self._bindings


def _refuse_server_arguments_that_cannot_hold(
    binding: KernelToolBinding, pool_seeds: PoolSeeds
) -> None:
    """Raise ``ImproperlyConfigured`` naming the tool."""
    try:
        check_server_arguments(binding.spec, binding.server_arguments, pool_seeds=pool_seeds)
    except ImproperlyConfigured as exc:
        raise ImproperlyConfigured(f"MCP tool {binding.name!r}: {exc}") from exc


__all__ = ["ToolRegistry"]
