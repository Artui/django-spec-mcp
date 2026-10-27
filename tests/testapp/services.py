from __future__ import annotations

from typing import Any

from django_service_specs import ServiceError

from tests.testapp.models import Invoice


def create_invoice(*, data: dict[str, Any]) -> Invoice:
    """Create a new invoice from validated input data."""
    return Invoice.objects.create(number=data["number"], amount_cents=data["amount_cents"])


def get_invoice(*, pk: int) -> Invoice:
    """Fetch a single invoice by primary key."""
    try:
        return Invoice.objects.get(pk=pk)
    except Invoice.DoesNotExist as exc:
        raise ServiceError(f"Invoice {pk} not found") from exc


def list_invoices() -> list[Invoice]:
    """Return every invoice."""
    return list(Invoice.objects.all())


def record_invoice(*, number: str, amount_cents: int = 0) -> Invoice:
    """Create an invoice from the validated values the kernel passes by name.

    The kernel twin of ``create_invoice``: a kernel service receives each
    validated value as its own keyword rather than one ``data`` mapping.
    """
    return Invoice.objects.create(number=number, amount_cents=amount_cents)


def invoice_by_pk(*, pk: int) -> Invoice | None:
    """Return the invoice ``pk`` names, or ``None``, which the kernel reports as not found.

    The kernel twin of ``get_invoice``, which raises a ``ServiceError`` instead.
    """
    return Invoice.objects.filter(pk=pk).first()
