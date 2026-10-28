"""``SelectableInvoicePresenter`` - an invoice narrowed to the fields the caller asked for."""

from __future__ import annotations

import dataclasses
from typing import Any

from django_service_specs import DataclassPresenter, Parameter, PresentationContext

from invoices.shapes import InvoiceOutput

FIELDS = Parameter(
    "fields",
    "array",
    items="string",
    # Every name the presenter can render, so the shape check refuses any other
    # before the selector runs: ``fields: ["items"]`` names the page envelope,
    # and no invoice has an ``items`` field.
    choices=tuple(field.name for field in dataclasses.fields(InvoiceOutput)),
    help="Invoice fields to return, e.g. id and number",
)
"""The ``fields`` shape ``invoices.list`` declares and this presenter reads."""


class SelectableInvoicePresenter(DataclassPresenter):
    """``InvoiceOutput``, narrowed by the call's ``fields`` shape.

    ``invoices.list`` declares ``FIELDS`` in its spec's ``shapes``, so the value
    a caller sends never reaches the selector or the FilterSet: it arrives here,
    on ``PresentationContext.shapes``. Any read-shaping Presenter works the same
    way; the transport never reads the value, only routes it.

    Two things a model calling the tool depends on:

    - **It runs per row.** On a paged tool the selection names an *item*'s
      fields, and the tool's advertised description says so.
    - **An unknown name is refused, not dropped.** Dropping it would hand back
      a page of empty rows, which reads as a real result. The declaration's
      ``choices`` refuse it before anything runs, as an ``isError``
      ``validation_error`` keyed by the argument, and the model can correct
      itself from that.
    """

    def __init__(self) -> None:
        super().__init__(InvoiceOutput)

    def presented(self, value: Any, context: PresentationContext | None) -> Any:
        data = self.present(value)
        wanted = context.shapes.get("fields") if context is not None else None
        if not wanted:
            return data
        return {name: data[name] for name in wanted}
