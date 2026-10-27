"""``kernel_tool_input_schema``'s two server-wide inputs: the pool seeds and the paging sentence.

Each is the owning server's: its ``pool_seeds=`` and its
``AgentConventions.query_param_on_pages``. The builder takes both, and these
tests hand them in directly. The listing does not pass them yet, so a server's
own value is held by a strict ``xfail`` each, which starts failing the day the
listing passes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp import AgentConventions, MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.schema.kernel_tool_input_schema import kernel_tool_input_schema

SEEDS = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme")
NOT_PASSED = "the listing does not pass the server's value to kernel_tool_input_schema yet"


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


@pytest.mark.xfail(strict=True, reason=NOT_PASSED)
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


@pytest.mark.xfail(strict=True, reason=NOT_PASSED)
def test_the_listing_reads_the_servers_paging_sentence() -> None:
    server = _server(conventions=AgentConventions(query_param_on_pages="Per row."))

    assert _listed(server)["properties"]["fields"]["description"] == "Per row."
