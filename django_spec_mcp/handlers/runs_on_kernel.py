from __future__ import annotations

from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding


def runs_on_kernel(binding: object) -> bool:
    """Whether ``binding`` carries a kernel spec, rather than being a view or a prompt."""
    return isinstance(binding, KernelToolBinding | KernelResourceBinding)


__all__ = ["runs_on_kernel"]
