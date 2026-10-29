from __future__ import annotations

from typing import Any

from django_service_specs import is_deactivated

from django_spec_mcp._compat.reject_awaitable import reject_awaitable
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.kernel_utils import principal_of
from django_spec_mcp.handlers.utils import scope_verdict
from django_spec_mcp.registry.types.prompt_binding import PromptBinding
from django_spec_mcp.registry.types.resource_binding import ResourceBinding


def judge_binding(
    binding: PromptBinding | ResourceBinding, token: TokenInfo
) -> tuple[bool, list[str]]:
    """Whether the caller ``token`` carries may use a prompt or a view: ``(allowed, required_scopes)``.

    Every path that serves or names a prompt or a view asks this, with the same
    answer: ``prompts/list`` and ``resources/list`` (through
    ``is_binding_listable``), ``prompts/get``, ``resources/read`` on both
    transports, ``completion/complete`` and a subscription's grant. A kernel
    resource's ``permissions`` are always empty, so this asks only its scope
    gate, and its spec's checks are the kernel's.

    **This stands in for the kernel's ``authorize``**, which cannot be asked:
    it runs ``kernel_spec`` on what it is handed, and a binding is not a spec.
    So the rule is restated here, in the order dispatch asks it:

    1. The scope gate (``scope_verdict``), which reads only the token and so is
       the cheapest refusal; every declared scope is ``required_scopes``.
    2. A deactivated principal is refused, as ``dispatch`` refuses one before
       any check, through the kernel's own ``is_deactivated``
       (``test_a_deactivated_principal_is_refused_a_prompt_as_a_spec_refuses_one``).
    3. Each check is asked ``has_permission(principal, binding)``, in order,
       the first refusal ending it. ``principal`` is ``principal_of(token.user)``,
       an ``AnonymousUser`` for no user, as a spec's dispatch is handed. Only the
       principal and the binding are passed: the keywords a spec's check may
       opt into (``arguments``, ``call_context``, a seed) are the kernel's to
       offer, so a check requiring one is a ``TypeError`` here.

    An answer still to be awaited is refused rather than read, since a
    coroutine is truthy and would admit every caller, as the kernel's
    ``sync_answer`` refuses one
    (``test_an_answer_still_to_be_awaited_is_refused_not_read_as_a_pass``).
    """
    allowed, required = scope_verdict(binding.scopes, token)
    if not allowed:
        return allowed, required
    principal: Any = principal_of(token.user)
    if is_deactivated(principal):
        return False, []
    for check in binding.permissions:
        asked = f"{type(check).__name__}.has_permission()"
        answer = reject_awaitable(
            check.has_permission(principal, binding),
            call=asked,
            remedy=(
                "A PermissionCheck is synchronous by contract: has_permission must answer a "
                "bool. Wrap any awaiting it needs in asgiref.sync.async_to_sync inside the "
                "method body."
            ),
            hazard="an un-awaited coroutine is truthy, so every caller would be granted access.",
        )
        if not answer:
            return False, []
    return True, []


__all__ = ["judge_binding"]
