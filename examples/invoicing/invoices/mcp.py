"""MCP server factory for the invoicing example.

All registrations live in one place so the wire surface is easy to
read in a single pass. Real projects can split this across multiple
modules (one per app) and combine them into a single ``MCPServer``.

Every operation is a ``django_service_specs`` spec: what it takes
(a ``Validator`` or ``reads``), who may run it (``permissions``), what it
returns (a ``Presenter``). The server only mounts them, so the same specs
could be served by any other transport the kernel has.
"""

from __future__ import annotations

from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    DataclassPresenter,
    FormValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import (
    AgentConventions,
    InMemorySessionStore,
    MCPServer,
    PromptArgument,
    PromptMessage,
)
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from invoices.filters import InvoiceFilterSet
from invoices.forms import InvoiceForm
from invoices.models import Invoice
from invoices.permissions import OpenPrompt
from invoices.presenters import FIELDS, SelectableInvoicePresenter
from invoices.selectors import (
    find_invoice,
    get_invoice,
    invoice_by_number,
    invoices_by_sent,
    list_invoices,
    outstanding_total,
)
from invoices.services import (
    apply_credit,
    create_invoice,
    mark_invoice_sent,
    set_invoice_amount,
)
from invoices.shapes import InvoiceOutput, OutstandingTotal

# Permissions are **required**: a spec declaring none is refused at
# registration, so an omission is an error rather than an open tool.
# ``Unrestricted()`` is the honest choice for a demo: it says "deliberately
# open" out loud, which is the whole point of the strict default. Swap it for
# a real ``PermissionCheck`` in anything real.
OPEN = [Unrestricted()]

# The target several tools act on, looked up by its number. Its ``reads`` are
# the lookup's arguments, so a tool with this as its ``instance_selector_spec``
# advertises ``number`` in its ``inputSchema``, required because the read says
# so, and a call without it is refused before the lookup runs.
BY_NUMBER = SelectorSpec(
    kind=SelectorKind.RETRIEVE,
    selector=invoice_by_number,
    reads=Parameters([Parameter("number", "string", required=True)]),
)

INVOICE = DataclassPresenter(InvoiceOutput)


def build_server() -> MCPServer:
    """Construct and populate the example MCP server."""
    server = MCPServer(
        name="invoicing-example",
        version="0.0.1",
        description="Demo invoicing MCP surface",
        # Dev-only: accepts any caller. Swap for the default
        # DjangoOAuthToolkitBackend (or your own) in production.
        auth_backend=AllowAnyBackend(),
        # Fine for single-process dev. The default DjangoCacheSessionStore
        # works across workers.
        session_store=InMemorySessionStore(),
        # What every spec on this mount may read without it being a tool
        # argument: here a fixed currency, which ``invoices.outstanding``
        # reads. A real project resolves it per caller, e.g.
        # ``currency=lambda *, user: user.organisation.currency``. A
        # registered name is reserved, so a client cannot send its own.
        pool_seeds=DEFAULT_POOL_SEEDS.extend(currency=lambda: "EUR"),
        # The sentences this server writes for the model, rather than any one
        # tool. Each field left alone keeps the package's wording; this one
        # tells a model that left out an invoice's number where to find it,
        # so ``invoices.set_amount`` called without ``number`` says so.
        conventions=AgentConventions(
            missing_arguments=(
                "Missing required argument(s): {names}. "
                "Look the invoice up with `invoices.list` if you do not have it."
            ),
        ),
    )

    # ----- Service tools (mutations) -----

    server.register_tool(
        ServiceSpec(
            service=create_invoice,
            validator=FormValidator(InvoiceForm),
            presenter=INVOICE,
            permissions=OPEN,
        ),
        name="invoices.create",
        description="Create a new invoice with a unique number and a positive amount.",
    )

    server.register_tool(
        ServiceSpec(
            service=mark_invoice_sent,
            reads=Parameters([Parameter("pk", "integer", required=True, minimum=1)]),
            presenter=INVOICE,
            permissions=OPEN,
        ),
        name="invoices.mark_sent",
        description="Flip an invoice's ``sent`` flag.",
    )

    server.register_tool(
        ServiceSpec(
            service=set_invoice_amount,
            # The target is looked up by number: the tool's ``inputSchema``
            # advertises the lookup's ``number`` beside ``amount_cents``.
            instance_selector_spec=BY_NUMBER,
            reads=Parameters([Parameter("amount_cents", "integer", required=True, minimum=0)]),
            # No output re-read: the service's own return is presented, so
            # its ``outputSchema`` keeps a strict ``required``.
            presenter=INVOICE,
            # Setting an amount twice is setting it once, so the tool lists
            # ``idempotentHint: true`` and a client may retry it freely.
            idempotent=True,
            permissions=OPEN,
        ),
        name="invoices.set_amount",
        description="Set the amount of the invoice with the given number.",
    )

    server.register_tool(
        ServiceSpec(
            service=apply_credit,
            # No Validator: ``credit_cents`` is one of the spec's ``reads``, so
            # the ``inputSchema`` lists it beside the lookup's ``number``. Under
            # the default ``unknown_arguments=REJECT`` that set is closed
            # (``additionalProperties: false``), and a call naming anything
            # else is a ``validation_error`` rather than an argument dropped
            # unread.
            instance_selector_spec=BY_NUMBER,
            reads=Parameters([Parameter("credit_cents", "integer", required=True)]),
            # Nothing is re-read: the service's own return is presented, and a
            # credit that settles the invoice returns ``None``.
            # ``allow_none=True`` says so, and the ``outputSchema`` admits the
            # ``{}`` that call is served as.
            presenter=INVOICE,
            allow_none=True,
            permissions=OPEN,
        ),
        name="invoices.apply_credit",
        description=(
            "Take a credit off the invoice with the given number; "
            "an empty object when the credit settles it."
        ),
    )

    # ----- Selector tools (reads) -----

    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_invoices,
            # Its filters are reads: the kernel reads each one as a Parameter,
            # so the ``inputSchema`` lists them and the closed argument set
            # admits them. Ordering is a filter like any other, declared on
            # the FilterSet rather than as a registration knob.
            filter_set=InvoiceFilterSet,
            # Field selection, which shapes what the presenter renders rather
            # than which rows are read, so it is a ``shape``: it never reaches
            # the selector or the FilterSet. Because the tool pages, the
            # advertised description gains a sentence saying the selection
            # applies to each item, never to the envelope; a selection that
            # names the envelope anyway comes back as a ``validation_error``
            # rather than as a page of empty rows, refused by the declaration's
            # ``choices`` before the selector runs.
            shapes=Parameters([FIELDS]),
            presenter=SelectableInvoicePresenter(),
            permissions=OPEN,
        ),
        name="invoices.list",
        description="List invoices, optionally filtered / ordered / paginated.",
        paginate=True,
    )

    # An argument the mount answers itself. ``sent`` is the selector's own
    # read; binding it here takes it out of the tool's ``inputSchema`` and
    # drops a client's value for it before anything reads it.
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=invoices_by_sent,
            reads=Parameters([Parameter("sent", "boolean", required=True)]),
            presenter=INVOICE,
            permissions=OPEN,
        ),
        name="invoices.unsent",
        description="Invoices not sent yet.",
        paginate=True,
        server_arguments=ServerArguments(values={"sent": False}),
    )

    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=find_invoice,
            reads=Parameters([Parameter("number", "string", required=True)]),
            presenter=INVOICE,
            # A miss is an answer, not an error: the call succeeds with
            # ``structuredContent: {}``, which the ``outputSchema`` admits.
            allow_none=True,
            permissions=OPEN,
        ),
        name="invoices.find",
        description="Find an invoice by number; an empty object when there is none.",
    )

    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            # Reads ``currency`` from the server's ``pool_seeds``.
            selector=outstanding_total,
            presenter=DataclassPresenter(OutstandingTotal),
            permissions=OPEN,
        ),
        name="invoices.outstanding",
        description="The total of unsent invoices, in the account's currency.",
    )

    # ----- Resource (single invoice by PK via URI template) -----

    # A resource is as reachable as a tool, so it declares its permissions
    # the same way. Its URI template's ``pk`` is the spec's read.
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=get_invoice,
            reads=Parameters([Parameter("pk", "integer", required=True)]),
            presenter=INVOICE,
            permissions=OPEN,
        ),
        name="invoice",
        uri_template="invoices://{pk}",
        description="A single invoice by primary key.",
    )

    # ----- Prompt (renders an email body for an invoice) -----

    def compose_invoice_email(*, pk: str) -> list[PromptMessage]:
        """Render an email body for a single invoice — illustrates a
        prompt that pulls live data from the database."""
        invoice = Invoice.objects.get(pk=int(pk))
        body: str = (
            f"Hello,\n\n"
            f"Invoice {invoice.number} for ${invoice.amount_cents / 100:.2f} "
            f"is now ready. Please remit at your convenience.\n\n"
            f"— Accounting"
        )
        return [PromptMessage.text(role="user", text=body)]

    server.register_prompt(
        name="compose_invoice_email",
        render=compose_invoice_email,
        description="Render a customer email body for an invoice.",
        arguments=[
            PromptArgument(name="pk", description="Invoice primary key", required=True),
        ],
        # A prompt reads the database here, so it is gated like everything
        # else on this server. Swap OpenPrompt for the real gate.
        permissions=[OpenPrompt()],
    )

    return server
