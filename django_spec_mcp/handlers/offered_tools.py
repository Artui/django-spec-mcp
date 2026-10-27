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

The answer is advisory. ``dispatch_spec`` still enforces every affordance at the
call, so a listing built a moment before a condition flips is answered, when the
stale entry is called, by an ``ActionUnavailable`` carrying the same ``code``.

Sync only, and that serves both transports: ``tools/list`` is a sync handler
that the async transport and ``alist_tools`` run in Django's thread-sensitive
executor, so a condition that queries is already off the event loop there and
drf-services' async twin would add a second hop for nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django_service_specs import Affordance, base_pool, operation_affordances
from django_service_specs import unmet_operation_affordance as kernel_unmet_affordance
from rest_framework_services import build_offline_context, unmet_operation_affordance
from rest_framework_services.types.service_spec import ServiceSpec

from django_spec_mcp.handlers.kernel_utils import call_context, carry_token, principal_of
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.registry.tool_registry import ToolBindingLike
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding


def offered_tools(
    bindings: list[ToolBindingLike], context: MCPCallContext
) -> tuple[list[ToolBindingLike], bool]:
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
    bindings: list[ToolBindingLike], context: MCPCallContext
) -> dict[str, Affordance]:
    """The bindings ``offered_tools`` leaves out, by name, each with the condition that did.

    The same pass over the same bindings, so a name is here exactly when the
    listing omits it: an in-process consumer that lists once and asks this each
    step offers what a fresh ``tools/list`` would, and can say why the rest are
    missing.
    """
    answers, _asked = _answer(bindings, context)
    return {binding.name: unmet for binding, unmet in answers if unmet is not None}


def declares_operation_conditions(bindings: Iterable[ToolBindingLike]) -> bool:
    """Whether any of ``bindings`` would be asked a condition at all.

    Pure Python over the declarations -- no pool, no query, no permission check
    -- so a caller can skip an executor hop when the answer is certain to be
    empty. ``True`` for exactly the bindings ``_answer`` asks about, so a skip
    taken on ``False`` cannot change a result.
    """
    return any(_asked_specs(binding) for binding in bindings)


def _answer(
    bindings: list[ToolBindingLike], context: MCPCallContext
) -> tuple[list[tuple[ToolBindingLike, Affordance | None]], bool]:
    """Each binding with the first condition refusing it now, or ``None``.

    ``always_listed=True`` answers ``None`` without asking, as it keeps a
    binding listed that ``FILTER_LISTINGS_BY_PERMISSIONS`` would drop: the name
    promises the binding is always in the list, and a discovery aid whose
    ``tools/call`` is refused with a ``code`` is the same trade the permission
    case already makes.

    **Pools are built only when needed.** Most tools declare no condition, so
    a pass over them builds nothing. A drf-services tool's pool is built once,
    by the first binding asking a condition, and every later one is asked
    against it, since its seeds are the same for every tool in one request. A
    kernel tool's pool is built per tool, because its call builds one per tool
    (``_kernel_unmet``).
    """
    pool: dict[str, Any] | None = None
    asked_a_kernel_tool = False
    answers: list[tuple[ToolBindingLike, Affordance | None]] = []
    for binding in bindings:
        gating: tuple[Any, ...] = _asked_specs(binding)
        if not gating:
            answers.append((binding, None))
            continue
        if isinstance(binding, KernelToolBinding):
            asked_a_kernel_tool = True
            answers.append((binding, _kernel_unmet(binding, context)))
            continue
        if pool is None:
            pool = _list_time_pool(context)
        answers.append((binding, _first_unmet(gating, pool, context.pool_seeds.reserved)))
    # A pool is built only for a binding that asks a condition, so either one
    # built means the listing is this caller's: the drfs half is held by
    # test_offered_tools_drfs.py::test_a_listing_that_asked_a_condition_is_private,
    # the kernel half by test_offered_tools.py's test of the same name.
    return answers, pool is not None or asked_a_kernel_tool


def _kernel_unmet(binding: KernelToolBinding, context: MCPCallContext) -> Affordance | None:
    """The first unmet operation condition of a kernel tool, asked as its call asks it.

    A kernel condition has no ``request`` seed: it reads the call's
    ``call_context``, which the call builds per tool, with the transport's
    request carrying the token and the tool's name as ``action``. Handed the
    drf-services pool instead, a condition reading ``call_context["request"]``
    raised at list time (``test_the_condition_sees_the_context_and_user_a_call_would``).

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
    return kernel_unmet_affordance(
        binding.spec, pool, reserved=context.pool_seeds.reserved, call_context=tool_context
    )


def _asked_specs(binding: ToolBindingLike) -> tuple[ServiceSpec[Any, Any, Any], ...]:
    """The specs a pass asks about for ``binding``: none when ``always_listed``.

    A conditional rather than an ``and``, so each way out is a branch coverage
    can see: test_always_listed_keeps_an_unavailable_tool holds the first, and
    test_a_listing_of_tools_declaring_nothing_builds_no_pool the empty gating.
    """
    return () if binding.always_listed else _gating_specs(binding)


def _first_unmet(
    gating: tuple[ServiceSpec[Any, Any, Any], ...],
    pool: dict[str, Any],
    reserved: frozenset[str],
) -> Affordance | None:
    """The first unmet condition across ``gating``, in order, or ``None``.

    Stops at the first spec refusing, as the listing always did.

    ``reserved`` is the server's ``pool_seeds.reserved``, the set dispatch hands
    the condition at the call: a callable condition sees only the reserved
    names of its pool, so without a registered seed in that set a condition
    reading it is asked here without it. The pool holding the seed's value is
    the other half (``_list_time_pool``), and each is held on its own by
    test_list_tools_and_unavailable_tools_ask_conditions_with_the_seeds.
    """
    for spec in gating:
        unmet: Affordance | None = unmet_operation_affordance(spec, pool, reserved=reserved)
        if unmet is not None:
            return unmet
    return None


def _gating_specs(binding: ToolBindingLike) -> tuple[ServiceSpec[Any, Any, Any], ...]:
    """The service specs whose operation conditions gate *every* call of ``binding``.

    A service tool's own spec. Never a selector tool's: a ``SelectorSpec``'s
    ``affordances`` names *other* operations to project onto its rows, not a
    condition on the read, so it gates nothing and asks nothing here.

    Only specs with an operation-scope condition are returned, as drf-services'
    ``operation_affordances`` selects them: the callable conditions, never the
    row conditions, and never a selector's. That is what keeps a listing with
    nothing to ask from building a pool, and from being served as private: a
    tool declaring only row conditions is listed exactly as one declaring
    nothing is, since there is no row at list time to ask them about.
    """
    specs: tuple[Any, ...] = (binding.spec,)
    return tuple(spec for spec in specs if operation_affordances(spec))


def _list_time_pool(context: MCPCallContext) -> dict[str, Any]:
    """The seeds a condition is asked against at list time, as the call would hand them.

    ``user`` is the token's user, as ``dispatch_spec`` receives it. ``request`` is
    the same kind of object a call's condition reads: the DRF ``Request`` that
    ``build_offline_context`` wraps around a copy of the transport's request,
    with the method forced to ``POST`` and ``query_params`` replaced by an empty
    mapping, as ``tools/call`` builds it. Handing the raw
    ``HttpRequest`` here, or ``None``, would let a condition reading ``request``
    answer differently in the list than at the call -- listing a tool that is
    then refused, or hiding one that would run.

    There are no call arguments at list time, so ``request.data`` is empty; a
    condition reading it was reading client input, which an operation-scope
    condition must not depend on.

    The server's ``pool_seeds`` are resolved into it as ``dispatch_spec``
    resolves them at the call, so a condition reading a registered seed (a
    tenant, a clock) is asked the same question in both places.
    """
    offline = build_offline_context(
        context.token.user, http_request=context.http_request, query_params={}
    )
    return base_pool(user=context.token.user, request=offline.request, seeds=context.pool_seeds)


__all__ = ["declares_operation_conditions", "offered_tools", "unavailable_tools"]
