from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django_service_specs import (
    Affordance,
    ServiceSpec,
    operation_affordances,
    seeded_pool,
    unmet_operation_affordance,
)

from django_spec_mcp.handlers.kernel_utils import call_context, carry_token, principal_of
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding


def offered_tools(
    bindings: list[KernelToolBinding], context: MCPCallContext
) -> tuple[list[KernelToolBinding], bool]:
    """``(the bindings to list, whether any condition was asked of this caller)``."""
    answers, asked = _answer(bindings, context)
    return [binding for binding, unmet in answers if unmet is None], asked


def unavailable_tools(
    bindings: list[KernelToolBinding], context: MCPCallContext
) -> dict[str, Affordance]:
    """The bindings ``offered_tools`` leaves out, by name, each with the condition that did."""
    answers, _asked = _answer(bindings, context)
    return {binding.name: unmet for binding, unmet in answers if unmet is not None}


def declares_operation_conditions(bindings: Iterable[KernelToolBinding]) -> bool:
    """Whether any of ``bindings`` would be asked a condition at all."""
    return any(_asked_specs(binding) for binding in bindings)


def _answer(
    bindings: list[KernelToolBinding], context: MCPCallContext
) -> tuple[list[tuple[KernelToolBinding, Affordance | None]], bool]:
    """Each binding with the first condition refusing it now, or ``None``."""
    asked = False
    answers: list[tuple[KernelToolBinding, Affordance | None]] = []
    for binding in bindings:
        if not _asked_specs(binding):
            answers.append((binding, None))
            continue
        asked = True
        answers.append((binding, _kernel_unmet(binding, context)))
    return answers, asked


def _kernel_unmet(binding: KernelToolBinding, context: MCPCallContext) -> Affordance | None:
    """The first unmet operation condition of a kernel tool, asked as its call asks it."""
    tool_context = call_context(carry_token(context.http_request, context.token), binding.name)
    pool = seeded_pool(
        user=principal_of(context.token.user),
        seeds=context.pool_seeds,
        call_context=tool_context,
    )
    return unmet_operation_affordance(
        binding.spec, pool, reserved=context.pool_seeds.reserved, call_context=tool_context
    )


def _asked_specs(binding: KernelToolBinding) -> tuple[ServiceSpec, ...]:
    """The specs a pass asks about for ``binding``: none when ``always_listed``."""
    return () if binding.always_listed else _gating_specs(binding)


def _gating_specs(binding: KernelToolBinding) -> tuple[ServiceSpec, ...]:
    """The service specs whose operation conditions gate *every* call of ``binding``."""
    specs: tuple[Any, ...] = (binding.spec,)
    return tuple(spec for spec in specs if operation_affordances(spec))


__all__ = ["declares_operation_conditions", "offered_tools", "unavailable_tools"]
