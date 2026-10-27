"""The dataclasses the kernel twins validate and present through."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from django_service_specs import FieldMarking


@dataclass
class InvoiceInput:
    """The kernel twin of ``InvoiceInputSerializer``."""

    number: str
    amount_cents: int = 0


@dataclass
class InvoiceOutput:
    """The kernel twin of ``InvoiceOutputSerializer``."""

    id: int
    number: str
    amount_cents: int
    sent: bool


@dataclass
class AgentInvoiceOutput:
    """The kernel twin of ``AgentInvoiceSerializer``: markings in ``Annotated`` extras."""

    id: Annotated[int, FieldMarking.handle("Invoice handle.")]
    number: Annotated[str, FieldMarking.label()]
    amount_cents: int
    sent: Annotated[bool, FieldMarking.hidden()]
    status: Literal["PENDING_REVIEW", "PAID"] = "PENDING_REVIEW"
