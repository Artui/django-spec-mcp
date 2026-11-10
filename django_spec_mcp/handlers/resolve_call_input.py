from __future__ import annotations

from typing import Any

from django_spec_mcp.elicitation.types.resolved_input import ResolvedInput
from django_spec_mcp.handlers.input_dispatch import resolve_prior_input
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding


def resolve_call_input(
    binding: KernelToolBinding,
    params: dict[str, Any],
    arguments: dict[str, Any],
    context: MCPCallContext,
) -> ResolvedInput:
    """``resolve_prior_input`` for a tool that takes part in a round trip."""
    if binding.is_selector:
        return ResolvedInput(arguments=arguments, fingerprint="")
    return resolve_prior_input(params, binding.name, arguments, context)


__all__ = ["resolve_call_input"]
