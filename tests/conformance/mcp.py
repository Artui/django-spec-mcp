from __future__ import annotations

from dataclasses import dataclass

from django_service_specs import (
    DataclassPresenter,
    RegisteredSpec,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.permissions import IsAuthenticated


@dataclass
class _Sentinel:
    sentinel: str


def _bulk_list() -> list[_Sentinel]:
    return [_Sentinel(sentinel="bulk")]


def _gated_service() -> dict[str, str]:
    return {"ok": "true"}


def build_conformance_server() -> MCPServer:
    """Build the conformance server, its caller anonymous unless a test signs one in."""
    server = MCPServer(
        name="conformance",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
    )
    server.register_tools(
        SpecRegistry(
            [
                RegisteredSpec(
                    name="conformance.bulk_listed",
                    spec=SelectorSpec(
                        kind=SelectorKind.LIST,
                        selector=_bulk_list,
                        presenter=DataclassPresenter(_Sentinel),
                        permissions=[Unrestricted()],
                    ),
                ),
                RegisteredSpec(
                    name="conformance.bulk_gated",
                    spec=ServiceSpec(service=_gated_service, permissions=[IsAuthenticated()]),
                ),
            ]
        ),
        overrides={
            "conformance.bulk_listed": {
                "description": "Registered via register_tools()",
                "paginate": True,
            },
            "conformance.bulk_gated": {
                "description": "Bulk registration honors the spec's permissions",
            },
        },
    )
    server.register_tool(
        ServiceSpec(service=_gated_service, permissions=[IsAuthenticated()]),
        name="conformance.gated",
        description="Refuses an anonymous caller.",
    )
    return server
