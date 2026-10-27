"""Ordering on a kernel selector tool, through its filter set or its own read.

The original's two channels survive on the kernel. A ``filter_set``'s
``OrderingFilter`` is read by the kernel's ``filter_set_parameters`` into the
spec's reads, so the tool advertises ``ordering`` with nothing declared at
registration, the value reaches the filter, and a value outside its choices
is refused by it. A sort the selector declares for itself is a ``reads``
``Parameter`` like any other. ``page`` and ``limit`` belong to the transport's
pagination, so neither reaches the selector, while the filter's reads do.

Changed by design, in the kernel's reading of a filter set:

- an ``OrderingFilter`` is a multiple choice, so it is advertised as an array
  of its choices and a bare string is refused for its shape, where the
  drf-services reflection advertised one string;
- its choices are advertised as an ``enum`` of the constants, without the
  labels (``Amount (descending)``) drf-services carried in a ``oneOf``.
  Whether the kernel should carry them is the kernel's question.

The original's ``ordering_fields=`` registration kwarg was already gone; on the
kernel ``register_tool`` has no such parameter, so it is not asked again. The
original is kept beside this file as ``test_selector_tool_ordering_drfs.py``.
"""

from __future__ import annotations

from typing import Any

import django_filters
import pytest
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from tests.testapp.kernel_shapes import InvoiceOutput
from tests.testapp.models import Invoice
from tests.utils import tool_error


class OrderedInvoiceFilterSet(django_filters.FilterSet):
    """The public vocabulary is ``amount`` / ``-amount``, not the ORM path."""

    sent = django_filters.BooleanFilter()
    ordering = django_filters.OrderingFilter(fields=(("amount_cents", "amount"),))

    class Meta:
        model = Invoice
        fields = ["sent"]


def _server(selector: Any, **spec: Any) -> MCPServer:
    paginate = spec.pop("paginate", False)
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=selector,
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
            **spec,
        ),
        name="invoices.list",
        paginate=paginate,
    )
    return server


def _filtered(**spec: Any) -> MCPServer:
    return _server(lambda: Invoice.objects.all(), filter_set=OrderedInvoiceFilterSet, **spec)


def _properties(server: MCPServer) -> dict[str, Any]:
    listed: Any = server.list_tools(user=None)
    return listed["tools"][0]["inputSchema"]["properties"]


def _numbers(out: Any) -> list[str]:
    assert not out.get("isError"), out
    payload = out["structuredContent"]
    rows = payload["items"] if isinstance(payload, dict) else payload
    return [row["number"] for row in rows]


def _call(server: MCPServer, arguments: dict[str, Any]) -> Any:
    return server.call_tool("invoices.list", arguments, user=None).to_dict()


@pytest.fixture
def three_invoices(db: None) -> None:
    for number, amount in (("mid", 200), ("low", 100), ("high", 300)):
        Invoice.objects.create(number=number, amount_cents=amount)


def test_the_filters_ordering_is_advertised_with_nothing_declared() -> None:
    assert _properties(_filtered())["ordering"] == {
        "type": "array",
        "items": {"type": "string", "enum": ["amount", "-amount"]},
        "maxItems": 1000,
    }


@pytest.mark.usefixtures("three_invoices")
@pytest.mark.parametrize(
    ("ordering", "expected"),
    [(["amount"], ["low", "mid", "high"]), (["-amount"], ["high", "mid", "low"])],
    ids=["ascending", "descending"],
)
def test_an_advertised_ordering_orders_the_rows(ordering: list[str], expected: list[str]) -> None:
    assert _numbers(_call(_filtered(), {"ordering": ordering})) == expected


@pytest.mark.django_db
def test_ordering_composes_with_a_filter_and_with_pagination() -> None:
    for number, amount, sent in (("b", 200, True), ("a", 100, True), ("x", 50, False)):
        Invoice.objects.create(number=number, amount_cents=amount, sent=sent)

    out = _call(
        _filtered(paginate=True), {"sent": True, "ordering": ["amount"], "page": 1, "limit": 1}
    )

    assert [row["number"] for row in out["structuredContent"]["items"]] == ["a"]
    assert out["structuredContent"]["totalPages"] == 2


@pytest.mark.django_db
def test_the_pagination_knobs_do_not_reach_the_selector_and_the_filters_reads_do() -> None:
    seen: dict[str, Any] = {}

    def greedy_selector(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return Invoice.objects.all()

    server = _server(greedy_selector, filter_set=OrderedInvoiceFilterSet, paginate=True)

    _call(server, {"ordering": ["amount"], "page": 1, "limit": 5})

    assert "page" not in seen
    assert "limit" not in seen
    assert seen["ordering"] == ["amount"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("ordering", "refused_for"),
    [(["nope"], "not one of the available choices"), ("amount", "Expected an array")],
    ids=["unknown-choice", "bare-string"],
)
def test_an_ordering_outside_the_filters_choices_is_refused(
    ordering: Any, refused_for: str
) -> None:
    error = tool_error(_call(_filtered(), {"ordering": ordering}))

    assert error["type"] == "validation_error"
    assert refused_for in str(error["detail"]["ordering"])


def _sorted_by(*, sort: str = "-amount_cents") -> Any:
    return Invoice.objects.order_by(sort)


def _ordered_by(*, ordering: str = "-amount_cents") -> Any:
    return Invoice.objects.order_by(ordering)


@pytest.mark.usefixtures("three_invoices")
@pytest.mark.parametrize(
    ("selector", "name"),
    [(_sorted_by, "sort"), (_ordered_by, "ordering")],
    ids=["sort", "named-ordering"],
)
def test_a_sort_the_selector_declares_is_advertised_and_applied(selector: Any, name: str) -> None:
    # Without a filter set, a selector may own a read named ``ordering`` too:
    # nothing in the transport takes the name.
    server = _server(selector, reads=Parameters([Parameter(name, "string")]))

    assert _properties(server)[name] == {"type": "string"}
    assert _numbers(_call(server, {name: "amount_cents"})) == ["low", "mid", "high"]
    assert _numbers(_call(server, {})) == ["high", "mid", "low"]
