"""A kernel ``LIST`` tool's queryset shaping, and the call's ``request`` its callables are handed.

Asks of kernel specs registered with ``register_tool``, against the real SQLite
model: empty shaping is a
no-op, ``annotations`` attach to each row, ``prefetch_related`` and
``select_related`` land on the queryset before ``extend_queryset``, which runs
last and may narrow it, shaping a selector that returns no queryset fails
loudly, and a paginated page is read once.

The kernel hands every callable that declares it the call's ``request`` and
``view`` (``CALL_SEEDS``): here the ``HttpRequest`` carrying the caller's token
as ``mcp_token``, and ``None`` for the view, since this transport has none.
That is asked here of ``extend_queryset`` and the selector. A kernel Presenter
takes no per-call context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.http import HttpRequest
from django_service_specs import (
    DataclassPresenter,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.types.context import MCPCallContext
from tests.testapp.kernel_shapes import InvoiceOutput
from tests.testapp.models import Invoice

pytestmark = pytest.mark.django_db

OPEN = [Unrestricted()]


@dataclass
class _Annotated:
    id: int
    number: str
    annotated_double: int


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _call(spec: SelectorSpec, arguments: dict[str, Any] | None = None, **knobs: Any) -> Any:
    server = _server()
    server.register_tool(spec, name="invoices.list", **knobs)
    context = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None, scopes=("read",)),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )
    return handle_tools_call({"name": "invoices.list", "arguments": arguments or {}}, context)


def _list(**fields: Any) -> SelectorSpec:
    fields.setdefault("presenter", DataclassPresenter(InvoiceOutput))
    return SelectorSpec(
        kind=SelectorKind.LIST, selector=Invoice.objects.all, permissions=OPEN, **fields
    )


def _seed(*amounts: int) -> None:
    for i, amount in enumerate(amounts):
        Invoice.objects.create(number=f"INV-{i}", amount_cents=amount, sent=False)


def test_empty_shaping_is_a_no_op() -> None:
    """Falsy shaping fields apply nothing: no join, no annotation, no extension."""
    _seed(100)
    out = _call(_list(select_related=(), prefetch_related=(), annotations={}, extend_queryset=None))
    assert len(out["structuredContent"]) == 1


def test_annotations_attach_to_each_row() -> None:
    _seed(100, 200)
    out = _call(
        _list(
            presenter=DataclassPresenter(_Annotated),
            annotations={"annotated_double": models.F("amount_cents") * 2},
        )
    )
    assert sorted(row["annotated_double"] for row in out["structuredContent"]) == [200, 400]


def test_extend_queryset_runs_last_and_is_handed_the_calls_request() -> None:
    """It sees the shaped queryset and can narrow it, and it is handed the
    call's ``request``, carrying the token, and no view."""
    _seed(100, 200, 300)
    seen: dict[str, Any] = {}

    def _narrow(*, queryset: Any, request: Any, view: Any) -> Any:
        seen.update(request=request, view=view)
        return queryset.filter(amount_cents__gte=200)

    out = _call(_list(extend_queryset=_narrow))
    assert sorted(row["number"] for row in out["structuredContent"]) == ["INV-1", "INV-2"]
    assert isinstance(seen["request"], HttpRequest)
    assert seen["request"].mcp_token.scopes == ("read",)
    assert seen["view"] is None


def test_the_selector_is_handed_the_calls_request() -> None:
    seen: list[Any] = []

    def _rows(*, request: Any) -> list[dict[str, str]]:
        seen.append(request)
        return [{"number": "A"}]

    out = _call(SelectorSpec(kind=SelectorKind.LIST, selector=_rows, permissions=OPEN))
    assert out["structuredContent"] == [{"number": "A"}]
    assert isinstance(seen[0], HttpRequest)


@pytest.mark.parametrize(
    ("field", "lookup", "read"),
    [
        ("prefetch_related", "bogus_relation", lambda qs: list(qs._prefetch_related_lookups)),
        ("select_related", "invalid_fk", lambda qs: list(qs.query.select_related)),
    ],
    ids=["prefetch_related", "select_related"],
)
def test_related_lookups_land_before_extend_queryset(field: str, lookup: str, read: Any) -> None:
    _seed(100)
    captured: list[Any] = []

    def _capture_and_strip(*, queryset: Any) -> Any:
        captured.extend(read(queryset))
        # A fresh queryset, so the lookup is never evaluated against a relation
        # the testapp model does not have.
        return Invoice.objects.all()

    _call(_list(**{field: (lookup,)}, extend_queryset=_capture_and_strip))
    assert captured == [lookup]


def test_shaping_a_selector_that_returns_no_queryset_fails_loudly() -> None:
    """A misconfiguration, raised rather than silently skipping the shaping."""
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=lambda: [{"number": "X"}],
        select_related=("ignored",),
        permissions=OPEN,
    )
    with pytest.raises(ImproperlyConfigured):
        _call(spec)


def test_a_paginated_page_is_read_once(django_assert_num_queries: Any) -> None:
    """The presenter iterates the page ``paginate_output`` fetched: a count and
    one page read, and no second fetch of the same rows."""
    _seed(1, 2, 3, 4, 5)
    with django_assert_num_queries(2):
        out = _call(_list(), {"limit": 2}, paginate=True)
    assert [row["number"] for row in out["structuredContent"]["items"]] == ["INV-0", "INV-1"]
