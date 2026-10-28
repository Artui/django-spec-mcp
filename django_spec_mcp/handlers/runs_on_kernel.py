from __future__ import annotations

from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding


def runs_on_kernel(binding: object) -> bool:
    """Whether ``binding`` carries a kernel spec, rather than being a view or a prompt.

    Every tool is a kernel tool; a resource is a kernel resource or a view (an
    MCP Apps UI resource, or one over a plain callable), and only the first
    has a spec whose own checks to ask. So the callers that take a resource or
    any listable binding ask this before they pick a path: both
    ``resources/read`` handlers, a subscription's grant, and
    ``is_binding_listable``. Asked of the binding's class, which is what
    carries the kernel spec, so no caller can decide by a field that another
    caller reads differently.
    """
    return isinstance(binding, KernelToolBinding | KernelResourceBinding)


__all__ = ["runs_on_kernel"]
