from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django_service_specs import DEFAULT_POOL_SEEDS, PoolSeeds

from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.judge_binding import judge_binding
from django_spec_mcp.handlers.kernel_utils import (
    admits_without_arguments,
    call_context,
    carry_token,
    principal_of,
)
from django_spec_mcp.handlers.runs_on_kernel import runs_on_kernel
from django_spec_mcp.handlers.utils import scope_verdict


def is_binding_listable(
    binding: Any,
    http_request: HttpRequest,
    token: TokenInfo,
    *,
    config: MCPConfig | None = None,
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
) -> bool:
    """Return ``True`` if the binding should appear in a list response."""
    if getattr(binding, "always_listed", False):
        return True
    if not runs_on_kernel(binding):
        return _binding_admits(binding, token)
    allowed, _ = scope_verdict(binding.scopes, token)
    if not allowed:
        return False
    return _kernel_spec_admits(
        binding, http_request, token, config or build_mcp_config(), pool_seeds
    )


def _binding_admits(binding: Any, token: TokenInfo) -> bool:
    """Whether ``judge_binding`` lets this caller past a prompt or a view, a raise hiding it."""
    try:
        allowed, _ = judge_binding(binding, token)
    except ImproperlyConfigured:
        raise
    except Exception:
        return False
    return allowed


def _kernel_spec_admits(
    binding: Any,
    http_request: HttpRequest,
    token: TokenInfo,
    config: MCPConfig,
    pool_seeds: PoolSeeds,
) -> bool:
    """Whether the kernel would let this caller past ``binding``'s spec's checks."""
    return admits_without_arguments(
        binding,
        principal_of(token.user),
        config=config,
        pool_seeds=pool_seeds,
        call_context=call_context(carry_token(http_request, token), binding.name),
    )


__all__ = ["is_binding_listable"]
