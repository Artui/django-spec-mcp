"""The prior-input step of ``tools/call``, which a kernel selector tool skips."""

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
    """``resolve_prior_input`` for a tool that takes part in a round trip.

    A selector tool takes no part in one: its ``inputResponses`` and
    ``requestState`` merge nothing and refuse nothing, so the read runs on the
    arguments as sent (``test_a_kernel_selector_ignores_a_round_trip``), while
    a service tool merges its answers
    (``test_kernel_service_input_rounds_still_merge``).
    """
    if binding.is_selector:
        return ResolvedInput(arguments=arguments, fingerprint="")
    return resolve_prior_input(params, binding.name, arguments, context)


__all__ = ["resolve_call_input"]
