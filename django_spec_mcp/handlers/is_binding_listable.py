"""Shared per-binding visibility check for the four list handlers.

Centralised so the four list handlers agree on what "listable" means: a binding
is hidden when any of its ``permissions`` denies the caller, unless
``always_listed=True`` opts it back in.

A permission may declare an ``is_listable(token)`` method for a list-time
specific decision — useful when its ``has_permission(request, token)`` reads
``request.arguments`` and would otherwise deny unfairly against the empty
list-time arguments. Without one, ``has_permission`` runs against a data-less
synthesised request, the right semantic for binding-level permissions such as
``ScopeRequired``.

A kernel binding's ``permissions`` hold only its scope gate: its spec's own
``PermissionCheck``s are the kernel's, so they are asked as well, through the
kernel's ``authorize``, the class-level question dispatch asks before it
resolves anything. A kernel check has no ``is_listable``; ``always_listed`` is
the way back for one that decides by the call's arguments.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    NotPermitted,
    PoolSeeds,
    PrincipalUnavailable,
    authorize,
)

from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.kernel_utils import (
    call_context,
    carry_token,
    principal_of,
    unguarded_grant,
)
from django_spec_mcp.handlers.runs_on_kernel import runs_on_kernel
from django_spec_mcp.handlers.utils import permission_verdict


def is_binding_listable(
    binding: Any,
    http_request: HttpRequest,
    token: TokenInfo,
    *,
    config: MCPConfig | None = None,
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
) -> bool:
    """Return ``True`` if the binding should appear in a list response.

    ``binding`` is duck-typed: all four binding dataclasses carry the same shape
    — a ``permissions`` tuple and an ``always_listed`` bool — without sharing a
    base class, and ``Any`` saves importing all four to spell a union.

    ``config`` and ``pool_seeds`` are the answering server's, and only a kernel
    binding reads them: the setting decides whether an unguarded spec's call is
    granted, and a check is offered the seeds a call would offer it. Absent,
    they are what a context built without a server holds.
    """
    if getattr(binding, "always_listed", False):
        return True
    for perm in binding.permissions:
        listable: Any = getattr(perm, "is_listable", None)
        if listable is not None:
            verdict = permission_verdict(
                perm,
                listable(token),
                method="is_listable",
                effect="every binding would be listed regardless of the caller.",
            )
            if not verdict:
                return False
            continue
        # List-time visibility equals call-time permission against a data-less
        # request: the pool seeds are still meaningful, only the caller-supplied
        # ``arguments`` payload is absent.
        allowed = permission_verdict(
            perm,
            perm.has_permission(http_request, token),
            method="has_permission",
            effect="every binding would be listed regardless of the caller.",
        )
        if not allowed:
            return False
    if runs_on_kernel(binding):
        return _kernel_spec_admits(
            binding, http_request, token, config or build_mcp_config(), pool_seeds
        )
    return True


def _kernel_spec_admits(
    binding: Any,
    http_request: HttpRequest,
    token: TokenInfo,
    config: MCPConfig,
    pool_seeds: PoolSeeds,
) -> bool:
    """Whether the kernel would let this caller past ``binding``'s spec's checks.

    Asked with what a call hands dispatch (``run_kernel_tool``), so a listing and
    a call cannot disagree about who passes: the same principal, the grant only
    the registrar's mark earns, the server's pool seeds, and a call context whose
    request carries the token. No arguments: a listing has none, and ``None`` is
    the kernel's word for that. A refusal (``NotPermitted``), or a deactivated
    principal (``PrincipalUnavailable``, which ``authorize`` raises when handed
    pool seeds, as dispatch does), hides the binding.
    """
    principal: Any = principal_of(token.user)
    grant = unguarded_grant(binding, principal, config)
    if grant is None and binding.spec.permissions is None:
        # A spec declaring no checks with no grant is refused at every call, so
        # it is listed to no one; asking ``authorize`` would raise its
        # configuration error and fail the whole listing instead. Both halves
        # are held: without the grant's, a marked binding granted by a server
        # with the setting off is hidden
        # (test_a_registered_unguarded_tool_is_listed_where_its_call_is_granted);
        # without the permissions', every guarded spec is
        # (test_a_tool_whose_checks_admit_the_caller_is_listed).
        return False
    try:
        authorize(
            binding.spec,
            principal,
            grant=grant,
            arguments=None,
            pool_seeds=pool_seeds,
            call_context=call_context(carry_token(http_request, token), binding.name),
        )
    except (NotPermitted, PrincipalUnavailable):
        # One arc to coverage, so each arm is named: without ``NotPermitted`` a
        # refused caller is answered an error
        # (test_a_tool_is_hidden_from_the_caller_its_spec_s_checks_refuse); without
        # ``PrincipalUnavailable`` so is a deactivated one
        # (test_a_deactivated_caller_is_listed_nothing).
        return False
    return True


__all__ = ["is_binding_listable"]
