"""The dataclasses the kernel twins validate and present through."""

from __future__ import annotations

from dataclasses import dataclass


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
