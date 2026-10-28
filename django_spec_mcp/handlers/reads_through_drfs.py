from __future__ import annotations

import inspect

from django_spec_mcp.auth.permissions.drf_permission_adapter import DRFPermissionAdapter
from django_spec_mcp.registry.types.resource_binding import ResourceBinding

_FILLABLE: tuple[object, ...] = (
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
    inspect.Parameter.KEYWORD_ONLY,
    inspect.Parameter.VAR_KEYWORD,
)


def reads_through_drfs(binding: ResourceBinding) -> bool:
    """Whether a resource that is not the kernel's reads through ``read_drfs_resource``.

    Asked by both ``resources/read`` handlers of a binding ``runs_on_kernel``
    turned down. A binding answering ``False`` needs nothing drf-services
    builds, and reads with no offline request or view, no object-level guard
    and no serializer: a view registered with ``register_ui_resource`` always
    does, having no output serializer, no ``kwargs`` provider and a selector
    taking nothing (``ui_view_to_resource`` refuses one that takes anything).
    A binding needs drf-services, and answers ``True``, when it holds any of:

    - an ``output_serializer``, which only drf-services renders
      (``test_a_resource_with_an_output_serializer_reads_through_drfs``);
    - a ``kwargs`` provider, called with the offline request and view
      (``test_a_resource_with_a_kwargs_provider_reads_through_drfs``);
    - a ``DRFPermissionAdapter``, whose class is judged on the read's view
      and again on the row it resolved
      (``test_a_view_guarded_by_a_drf_permission_reads_through_drfs``);
    - a selector taking anything from the pool, which hands it ``request``
      as drf-services' ``Request``
      (``test_a_resource_whose_selector_takes_the_pool_reads_through_drfs``).

    One arc to coverage, so each condition is named with the test that fails
    without it.
    """
    return (
        binding.output_serializer is not None
        or binding.kwargs_provider is not None
        or any(isinstance(permission, DRFPermissionAdapter) for permission in binding.permissions)
        or any(
            parameter.kind in _FILLABLE
            for parameter in inspect.signature(binding.selector).parameters.values()
        )
    )


__all__ = ["reads_through_drfs"]
