# A selector tool with filtering, ordering, and pagination

A service tool wraps a **mutation**: `register_tool` with a kernel
`ServiceSpec` runs the spec's `service` callable, inside
`transaction.atomic()` by default, and presents its result.

For **reads** you want a different shape. A "list invoices" tool is
read-only and benefits from filtering, ordering, and pagination — none
of which belong inside the selector. A `LIST` `SelectorSpec` keeps the
selector tiny (return a queryset); the spec's `filter_set` narrows and
orders it, and `paginate=True` at registration pages it.

```text
arguments → the kernel's argument check (the spec's reads, the FilterSet's filters)
          → run the selector
          → FilterSet(data=…).qs    (if spec.filter_set)
          → FilterSet applies ordering  (if it declares an OrderingFilter)
          → paginate                (if paginate=True)
          → the presenter, per row
          → ToolResult
```

Each piece is opt-in. A `SelectorSpec` with no filter set registered
without `paginate` behaves like a plain RPC read.

## Install django-filter

`filter_set=` takes a `django-filter` `FilterSet`, which the kernel's `filter`
extra installs:

```bash
pip install "django-service-specs[filter]"
```

Nothing in this package imports it, so a project with no filter set does not
need it.

## Define the pieces

A model:

```python
# invoices/models.py
from django.db import models


class Invoice(models.Model):
    number = models.CharField(max_length=32, unique=True)
    amount_cents = models.PositiveIntegerField()
    sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
```

What a row renders as, through the kernel's `DataclassPresenter`:

```python
# invoices/shapes.py
from dataclasses import dataclass
from datetime import datetime


@dataclass
class InvoiceOutput:
    id: int
    number: str
    amount_cents: int
    sent: bool
    created_at: datetime
```

A scoped selector — returns a raw queryset, no filtering or ordering:

```python
# invoices/selectors.py
from django.db.models import QuerySet

from invoices.models import Invoice


def list_invoices(*, user) -> QuerySet[Invoice]:
    """Return every invoice the caller is allowed to see."""
    return Invoice.objects.for_user(user)  # your scoping manager
```

A FilterSet describing the parametric reads the tool exposes:

```python
# invoices/filters.py
import django_filters

from invoices.models import Invoice


class InvoiceFilterSet(django_filters.FilterSet):
    sent = django_filters.BooleanFilter()
    min_amount = django_filters.NumberFilter(field_name="amount_cents", lookup_expr="gte")
    max_amount = django_filters.NumberFilter(field_name="amount_cents", lookup_expr="lte")
    created_after = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    # Ordering is declared here, like any other filter. The kernel reads it
    # into the tool's `inputSchema` as a choice over the names on the left —
    # the public vocabulary, not the ORM paths behind them.
    ordering = django_filters.OrderingFilter(
        fields=(("created_at", "created"), ("amount_cents", "amount")),
    )

    class Meta:
        model = Invoice
        fields = ["sent", "min_amount", "max_amount", "created_after"]
```

## Register the selector tool

```python
from django_service_specs import DataclassPresenter, SelectorKind, SelectorSpec, Unrestricted

from django_spec_mcp import MCPServer
from invoices.filters import InvoiceFilterSet
from invoices.selectors import list_invoices
from invoices.shapes import InvoiceOutput

server = MCPServer(name="invoicing", auth_backend=my_auth_backend)

server.register_tool(
    SelectorSpec(
        kind=SelectorKind.LIST,
        selector=list_invoices,
        filter_set=InvoiceFilterSet,
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[Unrestricted()],
    ),
    name="invoices.list",
    description="List invoices, optionally filtered / ordered / paginated.",
    paginate=True,
)
```

`filter_set` lives on the `SelectorSpec`, so the same declaration drives every
transport that serves the spec — declare the filterable shape once. Its
filters are reads: the kernel reads each one as an optional `Parameter`, so the
`inputSchema` lists it and the closed argument set admits it. Ordering rides
along with it: nothing about ordering appears on the registration call,
because the `OrderingFilter` already declared it. `paginate` is the one
MCP-only pipeline mechanic left here.

A `LIST` selector tool registered without `paginate=True` warns
`UnboundedListWarning`, and `SPEC_MCP["REQUIRE_LIST_PAGINATION"]` refuses it:
an unpaged list grows with the table until one call no longer fits a client's
context.

### Two ways to offer ordering

Ordering is not a registration knob, and there are two channels that
need none. Both reach the tool's `inputSchema`, so one declaration serves
every transport.

1. **An `OrderingFilter` on the `FilterSet`** — the one above. **Prefer
   this wherever there is a `FilterSet`**: the value is checked against
   a published set of choices before anything reaches the ORM, and the
   choices are consumer-facing names rather than column paths.
2. **A sort read the spec declares itself** — for a project with no
   `django-filter` dependency. A `Parameter` with `choices` is checked
   before the selector runs, so only the names you list reach it:

    ```python
    from django_service_specs import Parameter, Parameters

    SORTS = {"newest": "-created_at", "amount": "amount_cents"}


    def list_invoices(*, user, sort: str = "newest"):
        """List the caller's invoices, newest first by default."""
        return Invoice.objects.for_user(user).order_by(SORTS[sort])


    SelectorSpec(
        kind=SelectorKind.LIST,
        selector=list_invoices,
        reads=Parameters([Parameter("sort", "string", choices=tuple(SORTS))]),
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[Unrestricted()],
    )
    ```

!!! warning "Do not name that read `page` or `limit`"

    Those two names belong to a paginated tool's pipeline, which takes both
    from the arguments before the selector runs. A spec declaring a read
    named either is refused when it is registered with `paginate=True`. A
    `RETRIEVE` tool cannot paginate, so its selector may take either name.
    `ordering` is not reserved: name a read `ordering` if that reads best.

## Generated `inputSchema`

`tools/list` advertises the merged shape:

```json
{
  "type": "object",
  "properties": {
    "sent": {"type": "boolean"},
    "min_amount": {
      "type": "string",
      "format": "decimal",
      "minimum": -1e50,
      "maximum": 1e50,
      "description": "Matches `amount_cents` with the `gte` lookup."
    },
    "max_amount": {
      "type": "string",
      "format": "decimal",
      "minimum": -1e50,
      "maximum": 1e50,
      "description": "Matches `amount_cents` with the `lte` lookup."
    },
    "created_after": {
      "type": "string",
      "format": "date-time",
      "description": "Matches `created_at` with the `gte` lookup."
    },
    "ordering": {
      "type": "array",
      "items": {"type": "string", "enum": ["created", "-created", "amount", "-amount"]},
      "maxItems": 1000
    },
    "page": {"type": "integer", "minimum": 1},
    "limit": {"type": "integer", "minimum": 1, "maximum": 100}
  },
  "additionalProperties": false
}
```

Filter properties are always optional — they narrow the queryset but
aren't required to call the tool. Ordering takes a list of names, each
ascending (`field`) or descending (`-field`): `["-amount"]` is the largest
first. `page` defaults to `1` and `limit` to `100` when the model omits it.

A number filter is a `decimal` string, which its form field reads exactly; a
JSON number is still taken. The `description` on `min_amount`, `max_amount`
and `created_after` is derived, not written: where a filter's own name does
not give away which column it matches or with which lookup, the kernel states
both. A filter whose name, field and lookup already agree — `sent` — says
nothing extra. The kernel's
[declaring guide](https://artui.github.io/django-service-specs/declaring/)
has the full filter-to-`Parameter` mapping.

`limit`'s `maximum` is the effective [`MAX_PAGE_SIZE`](../reference/settings.md#outbound-bounds)
— the server-wide setting, or the `max_page_size=` passed at registration.
It is advertised here *and* clamped at dispatch, and it disappears from the
schema entirely when the bound is `None`. It is unrelated to `PAGE_SIZE`,
which bounds listing calls such as `tools/list` and never reaches a selector
tool's `limit`.

## Paginated response shape

When `paginate=True`, `tools/call` wraps the presented rows in a
pagination envelope:

```json
{
  "items": [<presented rows>],
  "page": 1,
  "totalPages": 7,
  "hasNext": true
}
```

Without `paginate`, the response is the presented list directly. Choose
based on how many rows your selector can return — paginate as soon as
the list could outgrow a single tool-call response.

## Arguments beside the filters

`filter_set` only describes the filter shape. If the tool also needs
arguments that are not filters, declare them as the spec's `reads`: they
join the FilterSet's filters in the `inputSchema`, and the selector receives
each one by name.

```python
from django_service_specs import Parameter, Parameters


def list_invoices(*, user, include_drafts: bool = False):
    invoices = Invoice.objects.for_user(user)
    return invoices if include_drafts else invoices.exclude(number__startswith="DRAFT-")


server.register_tool(
    SelectorSpec(
        kind=SelectorKind.LIST,
        selector=list_invoices,
        reads=Parameters([Parameter("include_drafts", "boolean", default=False)]),
        filter_set=InvoiceFilterSet,
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[Unrestricted()],
    ),
    name="invoices.list",
    description="List invoices, optionally with drafts.",
    paginate=True,
)
```

FilterSet-driven filters are applied after the selector returns its base
queryset. A read named like one of the FilterSet's filters but declared
differently is refused when the spec is built.

## When to reach for a service tool instead

If the operation creates / updates / deletes rows, register a `ServiceSpec`.
A service tool runs inside `transaction.atomic()` (by default) and presents
the service's return, not a queryset pipeline. Selector tools are the
read-shaped sibling — they should never have side effects.
