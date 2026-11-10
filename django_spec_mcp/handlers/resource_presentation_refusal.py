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
    """A presenter refusing a shape the URI carried, as the read refuses its arguments."""
    supplied: list[str] = [shape.name for shape in binding.spec.shapes if shape.name in variables]
    if not supplied:
        raise exc
    detail = exc.detail if isinstance(exc.detail, (list, dict)) else [exc.detail]
    key: str = supplied[0] if len(supplied) == 1 else "non_field_errors"
    return invalid_arguments_error({key: detail}, variables, config)


__all__ = ["resource_presentation_refusal"]
