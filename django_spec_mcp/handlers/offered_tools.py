"""Which tools ``tools/list`` offers: the ones no operation-scope affordance refuses now.

A service's ``affordances`` come in two kinds. A condition on the row (an ORM
expression) varies per object and is answered where it always is, at the call.
A callable condition varies with nothing the caller can pass -- it is answered
against the pool's seeds alone -- so when it is unmet, every call of the tool is
refused whatever its arguments. Offering such a tool only invites a refusal, and
the model spends a round trip learning what the server already knew.

This is not ``FILTER_LISTINGS_BY_PERMISSIONS``, and deliberately not gated by it.
That flag is off by default because a permission may depend on arguments that do
not exist at list time, so asking it early can deny unfairly. An operation-scope
condition cannot read arguments by construction, so that reason does not apply,
and a tool declaring no condition costs nothing here.

The answer is advisory. ``dispatch`` still enforces every affordance at the
call, so a listing built a moment before a condition flips is answered, when the
stale entry is called, by an ``ActionUnavailable`` carrying the same ``code``.

Sync only, and that serves both transports: ``tools/list`` is a sync handler
that the async transport and ``alist_tools`` run in Django's thread-sensitive
executor, so a condition that queries is already off the event loop there and
an async twin would add a second hop for nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django_service_specs import (
    Affordance,
    ServiceSpec,
    base_pool,
    operation_affordances,
    unmet_operation_affordance,
)

from django_spec_mcp.handlers.kernel_utils import call_context, carry_token, principal_of
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding


def offered_tools(
    bindings: list[KernelToolBinding], context: MCPCallContext
) -> tuple[list[KernelToolBinding], bool]:
    """``(the bindings to list, whether any condition was asked of this caller)``.

    Order is kept, so pagination over the result behaves as it does over the
    registry. The second element is what ``cacheScope`` derives from: a listing
    shaped by an answer asked against this caller's ``user`` and ``request`` is
    no longer byte-identical across callers, so ``public`` would licence a shared
    proxy to serve one caller's answer to another.
    """
    answers, asked = _answer(bindings, context)
    return [binding for binding, unmet in answers if unmet is None], asked


def unavailable_tools(
    bindings: list[KernelToolBinding], context: MCPCallContext
) -> dict[str, Affordance]:
    """The bindings ``offered_tools`` leaves out, by name, each with the condition that did.

    The same pass over the same bindings, so a name is here exactly when the
    listing omits it: an in-process consumer that lists once and asks this each
    step offers what a fresh ``tools/list`` would, and can say why the rest are
    missing.
    """
    answers, _asked = _answer(bindings, context)
    return {binding.name: unmet for binding, unmet in answers if unmet is not None}


def declares_operation_conditions(bindings: Iterable[KernelToolBinding]) -> bool:
    """Whether any of ``bindings`` would be asked a condition at all.

    Pure Python over the declarations -- no pool, no query, no permission check
    -- so a caller can skip an executor hop when the answer is certain to be
    empty. ``True`` for exactly the bindings ``_answer`` asks about, so a skip
    taken on ``False`` cannot change a result.
    """
    return any(_asked_specs(binding) for binding in bindings)


def _answer(
    bindings: list[KernelToolBinding], context: MCPCallContext
) -> tuple[list[tuple[KernelToolBinding, Affordance | None]], bool]:
    """Each binding with the first condition refusing it now, or ``None``.

    ``always_listed=True`` answers ``None`` without asking, as it keeps a
    binding listed that ``FILTER_LISTINGS_BY_PERMISSIONS`` would drop: the name
    promises the binding is always in the list, and a discovery aid whose
    ``tools/call`` is refused with a ``code`` is the same trade the permission
    case already makes.

    **Pools are built only when needed.** Most tools declare no condition, so
    a pass over them builds nothing. A tool's pool is built per tool, because
    its call builds one per tool (``_kernel_unmet``).
    """
    asked = False
    answers: list[tuple[KernelToolBinding, Affordance | None]] = []
    for binding in bindings:
        if not _asked_specs(binding):
            answers.append((binding, None))
            continue
        asked = True
        answers.append((binding, _kernel_unmet(binding, context)))
    # A pool is built only for a binding that asks a condition, so one built
    # means the listing is this caller's
    # (``test_a_listing_that_asked_a_condition_is_private``).
    return answers, asked


def _kernel_unmet(binding: KernelToolBinding, context: MCPCallContext) -> Affordance | None:
    """The first unmet operation condition of a kernel tool, asked as its call asks it.

    A kernel condition has no ``request`` seed: it reads the call's
    ``call_context``, which the call builds per tool, with the transport's
    request carrying the token and the tool's name as ``action``
    (``test_the_condition_sees_the_context_and_user_a_call_would``).

    **The pool is built per tool, from that same context**, as the call's
    dispatch builds it (the kernel's ``seeded_base``): ``base_pool`` with the
    context in, so a seed's resolver declaring ``request`` or ``view`` reads
    the context's, then the context taken back out, since it is no seed and
    the condition is handed it beside the pool. Built with no context, a
    resolver declaring ``request`` raised ``TypeError`` for the whole listing
    (test_a_seed_reading_the_request_is_resolved_for_the_listing_as_for_the_call),
    and one defaulting it hid a tool the call admits
    (test_a_seed_defaulting_the_request_does_not_hide_a_tool_the_call_admits).
    Per tool rather than once per listing because a resolver may declare
    ``call_context`` itself and read ``action``, which is this tool's name:
    a shared pool would hand every later tool the first one's
    (test_each_tool_s_seeds_are_resolved_with_its_own_action). The seeds
    resolve once per conditioned tool, as they resolve once per call.
    """
    tool_context = call_context(carry_token(context.http_request, context.token), binding.name)
    pool = base_pool(
        user=principal_of(context.token.user),
        seeds=context.pool_seeds,
        call_context=tool_context,
    )
    # The key ``base_pool`` was handed it under; the condition is handed the
    # same dict as ``call_context=`` instead, and the kernel refuses a pool
    # carrying a context beside a different one
    # (test_a_pool_is_built_per_conditioned_tool fails without this line).
    del pool["call_context"]
    return unmet_operation_affordance(
        binding.spec, pool, reserved=context.pool_seeds.reserved, call_context=tool_context
    )


def _asked_specs(binding: KernelToolBinding) -> tuple[ServiceSpec, ...]:
    """The specs a pass asks about for ``binding``: none when ``always_listed``.

    A conditional rather than an ``and``, so each way out is a branch coverage
    can see: test_always_listed_keeps_an_unavailable_tool holds the first, and
    test_a_listing_of_tools_declaring_nothing_builds_no_pool the empty gating.
    """
    return () if binding.always_listed else _gating_specs(binding)


def _gating_specs(binding: KernelToolBinding) -> tuple[ServiceSpec, ...]:
    """The service specs whose operation conditions gate *every* call of ``binding``.

    A service tool's own spec. Never a selector tool's: a ``SelectorSpec``'s
    ``affordances`` names *other* operations to project onto its rows, not a
    condition on the read, so it gates nothing and asks nothing here.

    Only specs with an operation-scope condition are returned, as the kernel's
    ``operation_affordances`` selects them: the callable conditions, never the
    row conditions, and never a selector's. That is what keeps a listing with
    nothing to ask from building a pool, and from being served as private: a
    tool declaring only row conditions is listed exactly as one declaring
    nothing is, since there is no row at list time to ask them about.
    """
    specs: tuple[Any, ...] = (binding.spec,)
    return tuple(spec for spec in specs if operation_affordances(spec))


__all__ = ["declares_operation_conditions", "offered_tools", "unavailable_tools"]
