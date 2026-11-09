"""Mutation-shaped callables. Each is registered as a service tool.

Services are pure Python functions — they don't know anything about
MCP. Each is called with the keywords it declares: the validated arguments
spread by name, the target row as ``instance``. The return value is rendered
through the spec's ``presenter``.
"""

from __future__ import annotations

import logging

from django_service_specs import ServiceError, ServiceValidationError

from invoices.models import Invoice

logger = logging.getLogger(__name__)


class InvoiceAlreadySent(Exception):
    """A project's own exception, which knows nothing of MCP or of the kernel.

    ``invoices/mcp.py`` maps it to the answer a model gets, through ``exception_map=``.
    """


def create_invoice(*, number: str, amount_cents: int) -> Invoice:
    """Create a new invoice. Returns the created instance for presenting."""
    return Invoice.objects.create(number=number, amount_cents=amount_cents)


def mark_invoice_sent(*, pk: int) -> Invoice:
    """Flip the ``sent`` flag on an existing invoice.

    Raises ``ServiceError`` when the invoice doesn't exist or is already sent —
    both are semantic errors, not input-shape errors. The MCP boundary answers
    it as an ``isError`` tool result, not a JSON-RPC error: its text is the JSON
    object ``{"error": {"type": "service_error", "message": ...}}``, which the
    model reads and can act on (``tests/test_examples_smoke.py`` holds the
    second-call answer).
    """
    try:
        invoice: Invoice = Invoice.objects.get(pk=pk)
    except Invoice.DoesNotExist as exc:
        raise ServiceError(f"Invoice {pk} not found") from exc
    if invoice.sent:
        raise ServiceError(f"Invoice {pk} is already sent")
    invoice.sent = True
    invoice.save(update_fields=["sent"])
    return invoice


def set_invoice_amount(*, instance: Invoice, amount_cents: int) -> Invoice:
    """Set an invoice's amount. ``instance`` is the row ``invoice_by_number`` found.

    Idempotent: the same call twice leaves the invoice as one call did, which
    the spec declares with ``idempotent=True`` and the tool lists as
    ``idempotentHint``. A sent invoice's amount is fixed: ``InvoiceAlreadySent`` is
    answered as the server's ``exception_map=`` says.
    """
    if instance.sent:
        raise InvoiceAlreadySent(f"Invoice {instance.number} is already sent")
    instance.amount_cents = amount_cents
    instance.save(update_fields=["amount_cents"])
    return instance


def write_off_invoice(*, number: str, reason: str = "") -> None:
    """Delete an invoice, logging why. ``reason`` is asked of a caller over MCP alone.

    ``WriteOffValidator`` declares it to that transport, so a call through any other
    leaves it out and this default stands.
    """
    deleted, _ = Invoice.objects.filter(number=number).delete()
    if not deleted:
        raise ServiceError(f"Invoice {number} not found")
    logger.info("Wrote off invoice %s: %s", number, reason)


def apply_credit(*, instance: Invoice, credit_cents: int) -> Invoice | None:
    """Take ``credit_cents`` off an invoice; ``None`` when the credit settles it.

    ``instance`` is the row ``invoice_by_number`` found. There is no
    Validator: ``credit_cents`` is one of the spec's ``reads``, so it is the
    tool's own argument, listed in its ``inputSchema`` and required because
    the read says so. A credit covering the whole amount
    deletes the invoice and presents nothing, which the spec declares with
    ``allow_none=True``.
    """
    if credit_cents <= 0:
        raise ServiceValidationError({"credit_cents": ["A credit must be positive."]})
    if credit_cents >= instance.amount_cents:
        instance.delete()
        return None
    instance.amount_cents -= credit_cents
    instance.save(update_fields=["amount_cents"])
    return instance
