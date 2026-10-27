from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.elicitation.types.resolved_input import ResolvedInput
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.schema.types.agent_conventions import AgentConventions


@dataclass(frozen=True)
class KernelRefusalScene:
    """What an answer in the kernel refusal map needs to know about the call.

    ``arguments`` are the ones the tool ran with, echoed in a validation
    result when ``INCLUDE_VALIDATION_VALUE`` is on. ``prior`` and ``context``
    are the wire's: an ``AdditionalInputRequired`` becomes an MRTR
    ``input_required`` result only where there is a round to carry the answers
    into, and the in-process ``call_tool`` passes ``None`` for both. ``span`` is
    the call's tracing span, or ``None``, for ``RECORD_SERVICE_EXCEPTIONS``.
    """

    arguments: Any
    config: MCPConfig
    conventions: AgentConventions
    prior: ResolvedInput | None = None
    context: MCPCallContext | None = None
    span: Any = None


__all__ = ["KernelRefusalScene"]
