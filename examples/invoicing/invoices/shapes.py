"""The dataclasses ``invoices/mcp.py`` presents results through.

A ``DataclassPresenter`` reads each declared field off a model row by
attribute, and the tool's ``outputSchema`` is read off the same declaration,
so neither knows anything about MCP.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass
class InvoiceOutput:
    """What every read surface in this example presents of an invoice."""

    id: int
    number: str
    amount_cents: int
    sent: bool
    created_at: dt.datetime


@dataclass
class OutstandingTotal:
    """What ``invoices.outstanding`` presents: a sum, in the mount's currency."""

    amount_cents: int
    currency: str
