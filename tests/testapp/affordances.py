"""One kernel declaration of ``affordances``, shared by the tests that advertise and call it.

Every condition is a callable rather than a condition on the row, so answering
one needs no query of its own. A kernel selector carries the answers on model
instances and mappings, and ``DataclassPresenter`` reads by attribute, so the
order a selector returns is an ``Invoice`` row: a test that calls a tool needs
one in the database, and a test that only lists the tools needs none.

The drf-services declaration is kept in ``affordances_drfs.py`` until the drfs
path is deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django_service_specs import (
    Affordance,
    DataclassPresenter,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from tests.testapp.models import Invoice


@dataclass
class Order:
    number: str


CANCEL_ORDER: ServiceSpec = ServiceSpec(
    service=lambda: None,
    permissions=[Unrestricted()],
    affordances=[
        Affordance(code="already_shipped", reason="The order has shipped.", when=lambda: True),
        Affordance(code="books_closed", reason="The books are closed.", when=lambda: False),
    ],
)
"""Met on its first condition and refused on its second, so every call answers
``books_closed``. Two conditions, so a schema enumerating only the one a row
happened to fail is told apart from one enumerating the declaration."""

DECLARED_CODES: list[str] = ["already_shipped", "books_closed"]


def order_selector_spec(kind: SelectorKind, **overrides: Any) -> SelectorSpec:
    """A selector spec presenting the one order, declaring ``cancel`` as an affordance."""
    fields: dict[str, Any] = {
        "kind": kind,
        "selector": (
            (lambda: Invoice.objects.get())
            if kind is SelectorKind.RETRIEVE
            else (lambda: Invoice.objects.all())
        ),
        "presenter": DataclassPresenter(Order),
        "affordances": {"cancel": CANCEL_ORDER},
        "permissions": [Unrestricted()],
        **overrides,
    }
    return SelectorSpec(**fields)


def fresh_server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
