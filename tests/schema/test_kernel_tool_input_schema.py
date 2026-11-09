"""``kernel_tool_input_schema``'s inputs: the pool seeds, the paging sentence and the context.

The first two are the owning server's: its ``pool_seeds=`` and its
``AgentConventions.query_param_on_pages``. The builder takes both: these tests
hand them in directly, and ask the listing that the server's own values reach it.
The context is the caller's to build, and what the listing hands is
``test_a_listing_reads_the_spec_under_its_stamp.py``'s to pin.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceSpec,
    Unrestricted,
    ValidationContext,
    Validator,
    build_call_context,
)

from django_spec_mcp import AgentConventions, MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.schema.kernel_tool_input_schema import kernel_tool_input_schema

SEEDS = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme")


@dataclass
class _Row:
    pk: int


def _server(**kwargs: Any) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None, **kwargs)
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda **_: [],
            reads=Parameters(
                [
                    Parameter("pk", "integer", required=True),
                    # Filled by a registered seed of its name, so never the caller's.
                    Parameter("tenant", "string", required=True, reflected=True),
                ]
            ),
            shapes=Parameters([Parameter("fields", "string")]),
            presenter=DataclassPresenter(_Row),
            permissions=[Unrestricted()],
        ),
        name="rows",
        description="Read rows.",
        paginate=True,
    )
    return server


def _binding(server: MCPServer) -> KernelToolBinding:
    binding = server.tools.get("rows")
    assert isinstance(binding, KernelToolBinding)
    return binding


def _listed(server: MCPServer) -> dict[str, Any]:
    listed: Any = server.list_tools(user=None)
    (tool,) = listed["tools"]
    return tool["inputSchema"]


def test_a_read_a_registered_seed_fills_is_not_advertised() -> None:
    schema = kernel_tool_input_schema(_binding(_server()), max_page_size=None, pool_seeds=SEEDS)

    assert "tenant" not in schema["properties"]
    assert "tenant" not in schema["required"]


def test_without_the_seed_the_read_is_the_callers() -> None:
    # The seed is what makes it the server's: on a mount registering none, a
    # reflected read is listed as declared.
    schema = kernel_tool_input_schema(_binding(_server()), max_page_size=None)

    assert "tenant" in schema["properties"]
    assert "tenant" in schema["required"]


def test_the_listing_reads_the_servers_pool_seeds() -> None:
    assert "tenant" not in _listed(_server(pool_seeds=SEEDS))["properties"]


def test_the_paging_sentence_is_the_one_passed() -> None:
    schema = kernel_tool_input_schema(
        _binding(_server()), max_page_size=None, query_param_on_pages="Per row."
    )

    assert schema["properties"]["fields"]["description"] == "Per row."


def test_none_advertises_a_paged_tools_shape_as_declared() -> None:
    schema = kernel_tool_input_schema(
        _binding(_server()), max_page_size=None, query_param_on_pages=None
    )

    assert schema["properties"]["fields"] == {"type": "string"}


def test_the_listing_reads_the_servers_paging_sentence() -> None:
    server = _server(conventions=AgentConventions(query_param_on_pages="Per row."))

    assert _listed(server)["properties"]["fields"]["description"] == "Per row."


def test_a_paged_tools_shape_the_server_answers_is_withheld_not_described() -> None:
    # Only a shape still listed is told it applies to each item: one the
    # server answers for is withheld before the sentence is laid on. A shape
    # named like a registered seed would be withheld too, but no call could run
    # it, so registration refuses that one.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda **_: [],
            shapes=Parameters([Parameter("fields", "string"), Parameter("tenant", "string")]),
            presenter=DataclassPresenter(_Row),
            permissions=[Unrestricted()],
        ),
        name="rows",
        description="Read rows.",
        paginate=True,
        server_arguments=ServerArguments(values={"tenant": "acme"}),
    )

    properties = _listed(server)["properties"]

    assert "tenant" not in properties
    assert "description" in properties["fields"]


class _OnlyToACall(Validator):
    def parameters(self, *, call_context: dict[str, Any] | None = None) -> Parameters:
        return (
            Parameters.of(Parameter("ref", "string", required=True))
            if call_context
            else Parameters([])
        )

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        return dict(arguments)


def _service_binding() -> KernelToolBinding:
    return KernelToolBinding(
        name="place",
        description="d",
        spec=ServiceSpec(
            service=lambda **_: None,
            validator=_OnlyToACall(),
            presenter=DataclassPresenter(_Row),
            permissions=[Unrestricted()],
        ),
    )


def test_the_context_handed_is_the_one_the_declaration_reads() -> None:
    schema = kernel_tool_input_schema(
        _service_binding(), max_page_size=None, call_context=build_call_context("mcp")
    )

    assert schema["required"] == ["ref"]


def test_with_no_context_the_schema_is_the_declaration_made_to_no_call() -> None:
    schema = kernel_tool_input_schema(_service_binding(), max_page_size=None)

    assert "ref" not in schema["properties"]
