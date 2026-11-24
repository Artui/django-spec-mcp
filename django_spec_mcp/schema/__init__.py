from django_spec_mcp.schema.agent_conventions import (
    HANDLE_DESCRIPTION,
    PAGED_QUERY_PARAM_SCOPE,
    PAGED_QUERY_PARAM_SCOPE_WITH_TOTAL,
    append_agent_conventions,
)
from django_spec_mcp.schema.types.agent_conventions import AgentConventions

__all__ = [
    "HANDLE_DESCRIPTION",
    "PAGED_QUERY_PARAM_SCOPE",
    "PAGED_QUERY_PARAM_SCOPE_WITH_TOTAL",
    "AgentConventions",
    "append_agent_conventions",
]
