"""``django-filter`` declarations driving ``invoices.list``.

The spec names the FilterSet as its ``filter_set``, and the kernel reads each
filter as an optional read, so the tool's ``inputSchema`` describes it and
the closed argument set admits it.
"""

from __future__ import annotations

import django_filters

from invoices.models import Invoice


class InvoiceFilterSet(django_filters.FilterSet):
    sent = django_filters.BooleanFilter()
    min_amount = django_filters.NumberFilter(field_name="amount_cents", lookup_expr="gte")
    max_amount = django_filters.NumberFilter(field_name="amount_cents", lookup_expr="lte")
    created_after = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    number = django_filters.CharFilter(lookup_expr="icontains")
    # Ordering is a filter like any other. ``OrderingFilter`` reads one key
    # holding several names, so the kernel reads it as an array whose items
    # choose among the public names on the right, each optionally prefixed
    # with ``-``, and never the ORM paths on the left. One declaration serves
    # every transport mounting the spec.
    ordering = django_filters.OrderingFilter(
        fields=(("created_at", "created"), ("amount_cents", "amount")),
    )

    class Meta:
        model = Invoice
        fields = ["sent", "min_amount", "max_amount", "created_after", "number"]
