from __future__ import annotations

from typing import Any

from django_service_specs import is_deactivated, sync_answer

from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.kernel_utils import principal_of
from django_spec_mcp.handlers.utils import scope_verdict
from django_spec_mcp.registry.types.prompt_binding import PromptBinding
from django_spec_mcp.registry.types.resource_binding import ResourceBinding


def judge_binding(
    binding: PromptBinding | ResourceBinding, token: TokenInfo
) -> tuple[bool, list[str]]:
    """Whether the caller ``token`` may use a prompt or a view."""
    allowed, required = scope_verdict(binding.scopes, token)
    if not allowed:
        return allowed, required
    principal: Any = principal_of(token.user)
    if is_deactivated(principal):
        return False, []
    for check in binding.permissions:
        answer = sync_answer(
            check.has_permission(principal, binding),
            asked=f"{type(check).__name__}.has_permission",
        )
        if not answer:
            return False, []
    return True, []


__all__ = ["judge_binding"]
