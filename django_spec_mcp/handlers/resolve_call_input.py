"""The prior-input step of ``tools/call``, which a kernel selector tool skips."""

from __future__ import annotations

from typing import Any

from django_spec_mcp.elicitation.types.resolved_input import ResolvedInput
from django_spec_mcp.handlers.input_dispatch import resolve_prior_input
from django_spec_mcp.handlers.runs_on_kernel import runs_on_kernel
from django_spec_mcp.handlers.types.context import MCPCallContext


def resolve_call_input(
    binding: Any, params: dict[str, Any], arguments: dict[str, Any], context: MCPCallContext
) -> ResolvedInput:
    """``resolve_prior_input`` for a tool that takes part in a round trip.

    A kernel selector tool takes no part in one, as no drf-services selector
    tool does: its ``inputResponses`` and ``requestState`` merge nothing and
    refuse nothing, so the read runs on the arguments as sent
    (``test_a_kernel_selector_ignores_a_round_trip``).

    Each conjunct is held by a test. Without the selector test, a kernel
    service tool stops merging its answers
    (``test_kernel_service_input_rounds_still_merge``); without the kernel
    test, a drf-services ``ToolBinding``, which has no ``is_selector``, makes
    every service call a 500
    (``test_the_question_comes_back_inside_an_ordinary_success``).
    """
    if runs_on_kernel(binding) and binding.is_selector:
        return ResolvedInput(arguments=arguments, fingerprint="")
    return resolve_prior_input(params, binding.name, arguments, context)


__all__ = ["resolve_call_input"]
