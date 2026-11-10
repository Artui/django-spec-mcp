from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.types.kernel_input_round import KernelInputRound
from django_spec_mcp.schema.types.agent_conventions import AgentConventions


@dataclass(frozen=True)
class KernelRefusalScene:
    """What an answer in the kernel refusal map needs to know about the call."""

    arguments: Any
    config: MCPConfig
    conventions: AgentConventions
    input_round: KernelInputRound | None = None
    span: Any = None


__all__ = ["KernelRefusalScene"]
