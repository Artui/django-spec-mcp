from __future__ import annotations

from django_service_specs import ServiceValidationError

from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.kernel_utils import invalid_arguments_error
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding


def resource_presentation_refusal(
    exc: ServiceValidationError,
    binding: KernelResourceBinding,
    variables: dict[str, str],
    config: MCPConfig,
) -> JsonRpcError:
    """A presenter refusing a shape the URI carried, as the read refuses its arguments.

    A template variable may name one of the spec's ``shapes``, and its value is
    read while the row is presented, after ``dispatch`` has returned. A
    ``ServiceValidationError`` from the presenter then blames the caller exactly
    when a tool's would (``present_kernel_result``): it is the ``-32602`` the
    shape check answers for the same value (``invalid_arguments_error``), keyed
    by the shape, or ``non_field_errors`` when the URI carried several, since
    nothing in the error says which one was refused. With no shape in the URI
    nothing the caller sent shaped the render, and ``exc`` is raised unchanged
    to the transport's internal error, as every other presenter failure is.

    A URI's variables are always strings and there are no server arguments on a
    resource, so a shape is sent exactly when its name is among ``variables``.
    """
    supplied: list[str] = [shape.name for shape in binding.spec.shapes if shape.name in variables]
    if not supplied:
        raise exc
    detail = exc.detail if isinstance(exc.detail, (list, dict)) else [exc.detail]
    key: str = supplied[0] if len(supplied) == 1 else "non_field_errors"
    return invalid_arguments_error({key: detail}, variables, config)


__all__ = ["resource_presentation_refusal"]
