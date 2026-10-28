"""Read-shaped callables. Each is registered as either a selector tool or a resource.

Selectors return querysets (for selector tools) or model instances
(for resource reads). They never filter, paginate, or sort — that's
the tool layer's job.
"""

from __future__ import annotations

from django.db.models import QuerySet, Sum

from invoices.models import Invoice
from invoices.shapes import OutstandingTotal


def list_invoices() -> QuerySet[Invoice]:
    """Base queryset for ``invoices.list``.

    Real projects usually scope this to the caller — e.g.
    ``Invoice.objects.for_user(user)``. This example is intentionally
    unscoped so the demo data is visible to every caller.
    """
    return Invoice.objects.all()


def get_invoice(*, pk: int) -> QuerySet[Invoice]:
    """Single invoice by primary key — backs the ``invoice`` resource.

    Returned as a queryset, so a URI naming no invoice is the kernel's
    not-found, which ``resources/read`` answers as a missing resource.
    """
    return Invoice.objects.filter(pk=pk)


def invoice_by_number(*, number: str) -> QuerySet[Invoice]:
    """The invoice ``invoices.set_amount`` acts on, looked up by its number.

    A service tool's target selector. Its spec's ``reads`` are the tool's
    lookup, so ``number`` is advertised in the tool's ``inputSchema`` beside
    the service's ``amount_cents``. The read is required and nothing on the
    server fills it, so a call without it is answered with a
    ``validation_error`` result naming ``number`` before the lookup runs.
    """
    return Invoice.objects.filter(number=number)


def find_invoice(*, number: str) -> QuerySet[Invoice]:
    """Backs ``invoices.find``, a RETRIEVE that may find nothing.

    Registered with ``allow_none=True``, so a number with no invoice is a
    successful call whose ``structuredContent`` is ``{}``, and the tool's
    ``outputSchema`` admits that beside a full row.
    """
    return Invoice.objects.filter(number=number)


def outstanding_total(*, currency: str) -> OutstandingTotal:
    """Backs ``invoices.outstanding``: the unsent total, in the mount's currency.

    ``currency`` is not a tool argument. It is a pool seed the server registers
    with ``pool_seeds=``, so every spec on the mount can read it, the tool's
    ``inputSchema`` does not advertise it, and a client argument of the same
    name is refused as an unknown argument rather than read.
    """
    total = Invoice.objects.filter(sent=False).aggregate(total=Sum("amount_cents"))["total"]
    return OutstandingTotal(amount_cents=total or 0, currency=currency)


def invoices_by_sent(*, sent: bool) -> QuerySet[Invoice]:
    """Backs ``invoices.unsent``: the invoices in one ``sent`` state.

    ``sent`` is a required read, so on its own it would be a required tool
    argument. The tool binds it with ``server_arguments=``
    instead, so this mount always answers ``False``: the tool's
    ``inputSchema`` does not advertise it, and a client sending ``sent`` has
    its value dropped (and logged by name) rather than reading sent invoices.
    """
    return Invoice.objects.filter(sent=sent).order_by("number")
