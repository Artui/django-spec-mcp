from __future__ import annotations

from typing import Any

from django_service_specs import DEFAULT_POOL_SEEDS, PoolSeeds, spec_caller_input_schema

from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.schema.agent_conventions import PAGED_QUERY_PARAM_SCOPE
from django_spec_mcp.schema.utils import end_sentence


def kernel_tool_input_schema(
    binding: KernelToolBinding,
    *,
    max_page_size: int | None,
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
    query_param_on_pages: str | None = PAGED_QUERY_PARAM_SCOPE,
    call_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The ``inputSchema`` a kernel tool advertises.

    The kernel's ``spec_caller_input_schema`` under the server's ``pool_seeds``
    and the binding's ``server_arguments`` and ``unknown_arguments``: what a
    caller must send, so the schema is closed exactly when the call refuses an
    undeclared key, and a service tool's parameters sit at the top level as the
    call takes them. The kernel withholds what the server fills and relaxes
    what a provider may fill, at the top level
    (``test_a_name_a_typed_provider_returns_is_not_asked_for``,
    ``test_an_untyped_provider_leaves_every_read_offered_and_none_required``,
    ``test_a_key_one_seat_may_decline_is_still_required_by_another``) and in
    each row of a ``many`` service, by what ``input_data`` reaches there
    (``test_a_row_field_a_typed_input_data_fills_is_neither_offered_nor_required``,
    ``test_a_rows_relaxing_stays_out_of_the_top_level``). What it adds is the
    transport's own:

    - a paginated ``LIST`` tool advertises ``page`` and ``limit``, ``limit`` bounded by ``max_page_size``
      when there is one, and each of its ``shapes`` says it applies to every
      item rather than to the envelope the ``outputSchema`` describes, in the
      words of ``query_param_on_pages`` (the server's
      ``AgentConventions.query_param_on_pages``, already rendered; ``None``
      leaves each shape as declared). A shape the server answers for is
      withheld by the kernel before the sentence is laid on.

    Off a ``many`` spec this is the reading the agent route's toolset makes of
    the kernel's schema, so one spec asks the same of a caller on both. A
    ``many`` spec's rows differ until that route reads them through
    ``spec_caller_input_schema`` too: there they are asked as declared.

    ``call_context`` is the dict a Validator declaring one is asked under, and the
    caller builds it. ``None``, the default, reads the declaration made to no call
    (``test_with_no_context_the_schema_is_the_declaration_made_to_no_call``);
    ``kernel_tool_entry`` hands the stamp a listing describes under.
    """
    schema = spec_caller_input_schema(
        binding.spec,
        pool_seeds=pool_seeds,
        server_arguments=binding.server_arguments,
        unknown_arguments=binding.unknown_arguments,
        call_context=call_context,
    )
    if binding.paginate:
        # ``spec_caller_input_schema`` builds the top level and its
        # ``properties`` fresh on every call, so they are this call's to extend.
        properties: dict[str, Any] = schema["properties"]
        if query_param_on_pages is not None:
            for name in binding.spec.shapes.names() & properties.keys():
                properties[name] = _on_each_item(properties[name], query_param_on_pages)
        properties["page"] = {"type": "integer", "minimum": 1}
        limit: dict[str, Any] = {"type": "integer", "minimum": 1}
        if max_page_size is not None:
            limit["maximum"] = max_page_size
        properties["limit"] = limit
    return schema


def _on_each_item(fragment: dict[str, Any], scope: str) -> dict[str, Any]:
    """A shape's property on a paged tool, told it applies to each item.

    Appended to the declared description rather than replacing it, which says
    what the shape is where this says where it lands, and standing alone when
    the shape declares none.
    """
    declared: str | None = fragment.get("description")
    return {**fragment, "description": f"{end_sentence(declared)} {scope}" if declared else scope}


__all__ = ["kernel_tool_input_schema"]
