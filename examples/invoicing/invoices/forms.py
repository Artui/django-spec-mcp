"""The Django form ``invoices.create`` validates its arguments with."""

from __future__ import annotations

from django import forms


class InvoiceForm(forms.Form):
    """Arguments for ``invoices.create``, through the kernel's ``FormValidator``.

    A form rather than a dataclass because it states the rules a dataclass
    cannot: a value breaking ``max_length`` or ``min_value`` is refused with
    the field's own message, keyed by the field, as an ``isError``
    ``validation_error`` result the model can correct from.
    """

    number = forms.CharField(max_length=32)
    amount_cents = forms.IntegerField(min_value=0)
