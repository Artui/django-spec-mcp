from __future__ import annotations

from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding


def runs_on_kernel(binding: object) -> bool:
    """Whether ``binding`` dispatches through the kernel rather than drf-services.

    The one question every caller that dispatches a tool or a resource asks
    before it picks a path: the sync and async ``tools/call`` handlers, the
    in-process ``call_tool``, ``tools/list``, both ``resources/read``
    handlers, and ``is_binding_listable``, which asks a kernel spec's own
    checks. Asked of the binding's class, which is what carries the kernel
    spec, so no caller can decide by a field that another caller reads
    differently.
    """
    return isinstance(binding, KernelToolBinding | KernelResourceBinding)


__all__ = ["runs_on_kernel"]
