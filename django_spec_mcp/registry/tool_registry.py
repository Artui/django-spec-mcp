from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import DEFAULT_POOL_SEEDS, PoolSeeds, check_server_arguments

from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding


class ToolRegistry:
    """Name to tool binding lookup.

    Holds service and selector tools in one namespace, rejecting
    duplicates loudly so a misconfigured project surfaces the conflict at
    registration rather than silently shadowing a tool.

    ``pool_seeds`` are the owning server's, which ``MCPServer`` passes: a
    binding's ``server_arguments`` are checked against them, since dispatch
    refuses a parameter a registered seed occupies.
    """

    def __init__(self, *, pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS) -> None:
        self._bindings: dict[str, KernelToolBinding] = {}
        self._pool_seeds: PoolSeeds = pool_seeds

    def register(self, binding: KernelToolBinding) -> None:
        """Add ``binding``, refusing a duplicate name or a binding of server values that cannot hold.

        The ``server_arguments`` check is made here rather than in the
        registrars alone, because a binding built by hand and handed straight to
        ``server.tools.register`` would otherwise reach every ``tools/call``
        unchecked, and fail each one with ``ImproperlyConfigured`` where it
        should have failed the server's startup
        (``test_a_binding_built_by_hand_is_checked_when_it_is_registered``).
        The registrars reach it as their last step, after the spec has compiled.
        """
        if binding.name in self._bindings:
            raise ValueError(f"Duplicate MCP tool name: {binding.name!r}")
        _refuse_server_arguments_that_cannot_hold(binding, self._pool_seeds)
        self._bindings[binding.name] = binding

    def get(self, name: str) -> KernelToolBinding | None:
        return self._bindings.get(name)

    def all(self) -> list[KernelToolBinding]:
        """Every binding, in **registration order**.

        ``tools/list`` has to be deterministic so clients can cache the catalog
        and cursor pagination stays stable, which dict insertion order already
        gives. Deliberately not sorted by name: registration order is authored
        order, which is a better first page for a model than an alphabetical
        one.
        """
        return list(self._bindings.values())

    def __len__(self) -> int:
        return len(self._bindings)

    def __contains__(self, name: object) -> bool:
        return name in self._bindings


def _refuse_server_arguments_that_cannot_hold(
    binding: KernelToolBinding, pool_seeds: PoolSeeds
) -> None:
    """Raise ``ImproperlyConfigured`` naming the tool for a binding of server values that cannot hold.

    The kernel's ``check_server_arguments`` decides, and names the spec's kind
    and callable; this adds the tool's name, which is what a server binding
    many tools needs to find the one at fault.

    A binding that binds nothing is not asked, because the kernel's check
    assembles the spec's parameters, and the assembly refuses declarations the
    registration does not: without the guard, a target lookup's filter named
    like an ``input_serializer`` field is refused here as two sources of one
    parameter, where it registers and fails each call
    (``test_a_lookup_filter_named_like_a_field_is_left_to_the_kernel``).
    """
    if not binding.server_arguments.names():
        return
    try:
        check_server_arguments(binding.spec, binding.server_arguments, pool_seeds=pool_seeds)
    except ImproperlyConfigured as exc:
        raise ImproperlyConfigured(f"MCP tool {binding.name!r}: {exc}") from exc


__all__ = ["KernelToolBinding", "ToolRegistry"]
