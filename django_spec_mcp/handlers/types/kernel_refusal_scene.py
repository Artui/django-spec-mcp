from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.types.kernel_input_round import KernelInputRound
from django_spec_mcp.schema.types.agent_conventions import AgentConventions


@dataclass(frozen=True)
class KernelRefusalScene:
    """What an answer in the kernel refusal map needs to know about the call.

    ``arguments`` are the ones the tool ran with, echoed in a validation
    result when ``INCLUDE_VALIDATION_VALUE`` is on. ``input_round`` is the
    round an ``AdditionalInputRequired`` can ask in, which only a service tool
    called over the wire has: ``None`` from the in-process ``call_tool`` and for
    a selector tool, where the request is answered as a service error. ``span`` is
    the call's tracing span, or ``None``, for ``RECORD_SERVICE_EXCEPTIONS``.
    """

    arguments: Any
    config: MCPConfig
    conventions: AgentConventions
    input_round: KernelInputRound | None = None
    span: Any = None


__all__ = ["KernelRefusalScene"]
