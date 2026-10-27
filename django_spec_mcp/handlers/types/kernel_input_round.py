from __future__ import annotations

from dataclasses import dataclass

from django_spec_mcp.elicitation.types.resolved_input import ResolvedInput
from django_spec_mcp.handlers.types.context import MCPCallContext


@dataclass(frozen=True)
class KernelInputRound:
    """The round an ``AdditionalInputRequired`` can ask its question in.

    The call's resolved prior input and its context, held together because
    ``ask_for_input`` needs both and neither means anything without the other:
    a call either has a round to carry the answers into or it does not.
    """

    prior: ResolvedInput
    context: MCPCallContext


__all__ = ["KernelInputRound"]
