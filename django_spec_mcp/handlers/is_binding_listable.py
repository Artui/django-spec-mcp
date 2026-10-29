"""Shared per-binding visibility check for the four list handlers.

Centralised so the four list handlers agree on what "listable" means: a binding
is hidden from a caller its call would refuse, unless ``always_listed=True``
opts it back in.

A prompt or a view is judged by ``judge_binding``, the judgement its get or read
asks: its scope gate, then each of its kernel ``PermissionCheck``s asked
``has_permission(principal, binding)``. A check has no list-time hook of its
own, so the listing and the call cannot disagree. A check that raises hides
its binding and no other, as on the kernel arm below, while an
``ImproperlyConfigured`` fails the listing as it fails the call.

A kernel binding is judged by its scope gate (``scope_verdict``) and then its
spec's own ``PermissionCheck``s, which are the kernel's, through the kernel's
``authorize``, the class-level question dispatch asks before it resolves
anything, and, for a create-shaped service spec, through ``authorize_scope`` of
``None``, the one scope question dispatch asks of it that reads no row. A check
that decides by the call's arguments is handed none at list time and so cannot
answer, which hides its binding, and ``always_listed`` is the way back.
"""

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
    """Return ``True`` if the binding should appear in a list response.

    ``binding`` is any of the four binding dataclasses, which share no base
    class; ``Any`` saves importing all four to spell a union, and
    ``runs_on_kernel`` is what tells them apart.

    ``config`` and ``pool_seeds`` are the answering server's, and only a kernel
    binding reads them: the setting decides whether an unguarded spec's call is
    granted, and a check is offered the seeds a call would offer it. Absent,
    they are what a context built without a server holds.
    """
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
    """Whether ``judge_binding`` lets this caller past a prompt or a view, a raise hiding it.

    Mirrors the kernel arm (``admits_without_arguments``), for the same reasons.
    """
    try:
        allowed, _ = judge_binding(binding, token)
    except ImproperlyConfigured:
        # A configuration error the get or read raises as well, so the listing
        # fails with it rather than hiding the binding and leaving the error
        # for later (test_a_check_misconfigured_at_call_time_still_fails_the_listing).
        raise
    except Exception:
        # Refused, and only this binding. A check that cannot decide raises,
        # and failing the listing for it would hide every other prompt or view
        # too (test_a_prompt_check_that_raises_hides_that_prompt_and_no_other,
        # test_a_view_check_that_raises_hides_that_view_and_no_other).
        return False
    return allowed


def _kernel_spec_admits(
    binding: Any,
    http_request: HttpRequest,
    token: TokenInfo,
    config: MCPConfig,
    pool_seeds: PoolSeeds,
) -> bool:
    """Whether the kernel would let this caller past ``binding``'s spec's checks.

    ``admits_without_arguments``, the very question a call failing its shape
    check is asked, so the two cannot drift: a filtered listing hides a tool
    from exactly the callers it refuses before telling them which argument
    they left out. ``always_listed`` returns before this is asked, and so
    lists a tool without changing that answer.
    """
    return admits_without_arguments(
        binding,
        principal_of(token.user),
        config=config,
        pool_seeds=pool_seeds,
        call_context=call_context(carry_token(http_request, token), binding.name),
    )


__all__ = ["is_binding_listable"]
