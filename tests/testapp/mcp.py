from __future__ import annotations

from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.auth_backend import MCPAuthBackend
from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.kernel_shapes import InvoiceInput, InvoiceOutput
from tests.testapp.services import invoice_by_pk, list_invoices, record_invoice


def build_server(
    *, config: MCPConfig | None = None, auth_backend: MCPAuthBackend | None = None
) -> MCPServer:
    """Construct the test MCP server: three tools and a resource, as kernel specs."""
    server = MCPServer(
        name="testapp",
        auth_backend=auth_backend or AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        config=config,
    )
    server.register_tool(
        ServiceSpec(
            service=record_invoice,
            validator=DataclassValidator(InvoiceInput),
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
        ),
        name="invoices.create",
        description="Create an invoice.",
    )
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=invoice_by_pk,
            reads=Parameters([Parameter("pk", "integer", required=True)]),
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
        ),
        name="invoices.get",
        description="Fetch a single invoice by id.",
    )
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_invoices,
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
        ),
        name="invoices.list",
        description="List every invoice.",
        paginate=True,
    )
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=invoice_by_pk,
            reads=Parameters([Parameter("pk", "integer", required=True)]),
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
        ),
        name="invoice",
        uri_template="invoices://{pk}",
        description="Fetch a single invoice by id.",
    )
    return server


def build_kernel_server(
    *, config: MCPConfig | None = None, auth_backend: MCPAuthBackend | None = None
) -> MCPServer:
    """``build_server`` with ``HeaderAuthBackend`` unless a test hands its own."""
    return build_server(config=config, auth_backend=auth_backend or HeaderAuthBackend())
