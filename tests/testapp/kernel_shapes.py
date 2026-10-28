"""The dataclasses the kernel specs validate and present through."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from django_service_specs import FieldMarking


@dataclass
class InvoiceInput:
    """An invoice as the caller sends it."""

    number: str
    amount_cents: int = 0


@dataclass
class InvoiceOutput:
    """An invoice as the service presents it."""

    id: int
    number: str
    amount_cents: int
    sent: bool


@dataclass
class AgentInvoiceOutput:
    """An invoice presented with audience markings in ``Annotated`` extras."""

    id: Annotated[int, FieldMarking.handle("Invoice handle.")]
    number: Annotated[str, FieldMarking.label()]
    amount_cents: int
    sent: Annotated[bool, FieldMarking.hidden()]
    status: Literal["PENDING_REVIEW", "PAID"] = "PENDING_REVIEW"
