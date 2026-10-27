from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    Parameters,
    PoolSeeds,
    SelectorSpec,
    ServiceSpec,
    provider_keys,
    server_owned_keys,
    spec_input_schema,
)

from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.schema.agent_conventions import PAGED_QUERY_PARAM_SCOPE
from django_spec_mcp.schema.utils import end_sentence


def kernel_tool_input_schema(
    binding: KernelToolBinding,
    *,
    max_page_size: int | None,
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
    query_param_on_pages: str | None = PAGED_QUERY_PARAM_SCOPE,
) -> dict[str, Any]:
    """The ``inputSchema`` a kernel tool advertises.

    The kernel's ``spec_input_schema`` under the binding's ``unknown_arguments``,
    so the schema is closed exactly when the call refuses an undeclared key, and
    a service tool's parameters sit at the top level as the call takes them.
    The kernel lists every parameter as declared, ``required`` meaning the call
    needs the value from someone, and leaves to the transport what it answers
    itself. So:

    - a name the server fills whatever the caller sends is left out of
      ``properties`` and ``required``: the binding's ``server_arguments``, and
      the kernel's ``server_owned_keys`` for ``pool_seeds``, which holds the
      keys a typed ``kwargs=`` or ``input_data=`` provider always fills
      (``test_a_name_a_typed_provider_returns_is_not_asked_for``);
    - a parameter a provider may fill is offered and not required, because
      dispatch does not demand it of the caller (``_may_fill``);
    - a paginated ``LIST`` tool advertises ``page`` and ``limit``, as the
      drf-services selector schema does, ``limit`` bounded by ``max_page_size``
      when there is one, and each of its ``shapes`` says it applies to every
      item rather than to the envelope the ``outputSchema`` describes, in the
      words of ``query_param_on_pages`` (the server's
      ``AgentConventions.query_param_on_pages``, already rendered; ``None``
      leaves each shape as declared).

    The same reading the agent route's toolset makes of the kernel's schema, so
    one spec asks the same of a caller on both.
    """
    spec = binding.spec
    schema: dict[str, Any] = dict(
        spec_input_schema(spec, unknown_arguments=binding.unknown_arguments)
    )
    withheld: frozenset[str] = binding.server_arguments.names() | server_owned_keys(
        spec, pool_seeds=pool_seeds
    )
    properties: dict[str, Any] = {
        name: fragment
        for name, fragment in (schema.get("properties") or {}).items()
        if name not in withheld
    }
    if binding.paginate:
        if query_param_on_pages is not None:
            for name in spec.shapes.names() & properties.keys():
                properties[name] = _on_each_item(properties[name], query_param_on_pages)
        properties["page"] = {"type": "integer", "minimum": 1}
        limit: dict[str, Any] = {"type": "integer", "minimum": 1}
        if max_page_size is not None:
            limit["maximum"] = max_page_size
        properties["limit"] = limit
    schema["properties"] = properties
    unasked: frozenset[str] = withheld | _may_fill(spec)
    required = [name for name in schema.pop("required", []) if name not in unasked]
    if required:
        schema["required"] = required
    return schema


def _may_fill(spec: ServiceSpec | SelectorSpec) -> frozenset[str]:
    """Every top-level parameter a provider may fill without always filling it.

    Each provider's values reach one seat: a ``kwargs=`` provider its spec's
    ``reads``, a service's target selector's ``kwargs=`` that selector's
    ``reads``, and ``input_data=`` the Validator's fields. In its seat, a typed
    provider may fill the keys it may decline with ``UNSET``, and one whose
    return says nothing about its keys may fill every parameter. Read with the
    kernel's public ``provider_keys``, standing in for the reader its dispatch
    relaxes the same parameters with, which it does not export
    (``dispatch.utils.provider_reached``).

    The Validator's seat is read only on a service that has a Validator and is
    not ``many``, whose fields are each row's rather than top-level arguments.
    One arc to coverage, each conjunct held by its own test: a selector has no
    Validator (every case of
    ``test_an_untyped_provider_leaves_every_read_offered_and_none_required``),
    a service may have none
    (``test_a_service_without_a_validator_relaxes_its_reads_alone``) and a
    ``many`` spec's rows are no seat
    (``test_an_untyped_input_data_leaves_a_many_specs_list_required``).
    """
    seats: list[tuple[Callable[..., Any] | None, Parameters]] = [(spec.kwargs, spec.reads)]
    if isinstance(spec, ServiceSpec):
        target = spec.target_selector_spec()
        if target is not None:
            seats.append((target.kwargs, target.reads))
        if spec.validator is not None and not spec.many:
            seats.append((spec.input_data, spec.validator.parameters()))
    reached: set[str] = set()
    for provider, parameters in seats:
        keys = provider_keys(provider)
        names = parameters.names()
        reached |= names if keys is None else names & keys.declinable
    return frozenset(reached)


def _on_each_item(fragment: dict[str, Any], scope: str) -> dict[str, Any]:
    """A shape's property on a paged tool, told it applies to each item.

    Appended to the declared description rather than replacing it, which says
    what the shape is where this says where it lands, and standing alone when
    the shape declares none, as a ``QueryParam`` is on the drf-services path.
    """
    declared: str | None = fragment.get("description")
    return {**fragment, "description": f"{end_sentence(declared)} {scope}" if declared else scope}


__all__ = ["kernel_tool_input_schema"]
