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


def record_invoice(*, number: str = "", amount_cents: int = 0) -> Invoice:
    """Create an invoice from the validated values the kernel passes by name.

    A kernel service receives each validated value as its own keyword rather
    than one ``data`` mapping.
    ``number`` has a default because many specs register this service bare,
    to ask a registration knob with no Validator or read declaring the
    keyword, and registration refuses a keyword nothing fills
    (``check_seeds``).
    """
    return Invoice.objects.create(number=number, amount_cents=amount_cents)


def invoice_by_pk(*, pk: int) -> Invoice | None:
    """Return the invoice ``pk`` names, or ``None``, which is reported as not found."""
    return Invoice.objects.filter(pk=pk).first()
