from __future__ import annotations

from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    Unrestricted,
)
from django_service_specs import SelectorSpec as KernelSelectorSpec
from django_service_specs import ServiceSpec as KernelServiceSpec
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.auth_backend import MCPAuthBackend
from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.kernel_shapes import InvoiceInput, InvoiceOutput
from tests.testapp.serializers_drfs import InvoiceInputSerializer, InvoiceOutputSerializer
from tests.testapp.services import (
    create_invoice,
    get_invoice,
    invoice_by_pk,
    list_invoices,
    record_invoice,
)


def build_server(*, config: MCPConfig | None = None) -> MCPServer:
    """Construct the test MCP server with two tools and two resources.

    A factory rather than a module-level instance keeps tests independent —
    each test that needs its own configuration can build a fresh server.

    ``config`` overrides the scalars. Since they are resolved once in
    ``MCPServer.__init__``, a test that needs non-default scalars must build its
    own server (and mount it via ``urlconf_for``) rather than mutating
    ``settings.SPEC_MCP`` around an already-mounted one.
    """
    server = MCPServer(
        name="testapp",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        config=config,
    )

    server.register_service_tool(
        name="invoices.create",
        spec=ServiceSpec(
            service=create_invoice,
            input_serializer=InvoiceInputSerializer,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                output_serializer=InvoiceOutputSerializer,
            ),
        ),
        description="Create an invoice.",
    )
    server.register_resource(
        name="invoice",
        uri_template="invoices://{pk}",
        selector=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=get_invoice,
            output_serializer=InvoiceOutputSerializer,
        ),
        description="Fetch a single invoice by id.",
    )
    server.register_resource(
        name="invoices",
        uri_template="invoices://",
        selector=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_invoices,
            output_serializer=InvoiceOutputSerializer,
        ),
        description="List all invoices.",
    )
    return server


def build_kernel_server(
    *, config: MCPConfig | None = None, auth_backend: MCPAuthBackend | None = None
) -> MCPServer:
    """Construct the kernel twin of ``build_server``: the same operations as kernel specs.

    ``invoices.create`` and the ``invoice`` resource are twins of the
    drf-services ones above, with the same names, so a test can send one
    request to both servers and compare what each answers. The ``invoices``
    list resource has no twin, since a kernel resource is a ``RETRIEVE``; the
    list is the ``invoices.list`` selector tool here, beside ``invoices.get``.

    The auth backend is passed explicitly, ``HeaderAuthBackend`` unless a test
    hands its own, so the caller is chosen per request by header.
    """
    server = MCPServer(
        name="testapp-kernel",
        auth_backend=auth_backend or HeaderAuthBackend(),
        session_store=InMemorySessionStore(),
        config=config,
    )
    server.register_tool(
        KernelServiceSpec(
            service=record_invoice,
            validator=DataclassValidator(InvoiceInput),
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
        ),
        name="invoices.create",
        description="Create an invoice.",
    )
    server.register_tool(
        KernelSelectorSpec(
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
        KernelSelectorSpec(
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
        KernelSelectorSpec(
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
