from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import SelectorKind, SelectorSpec, ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.services import invoice_by_pk, list_invoices, record_invoice

OPEN = [Unrestricted()]


def _seat(kind: SelectorKind, selector: Any) -> SelectorSpec:
    return SelectorSpec(kind=kind, selector=selector, permissions=OPEN)


@pytest.mark.parametrize(
    ("seat", "kind", "selector"),
    [
        ("instance_selector_spec", SelectorKind.RETRIEVE, invoice_by_pk),
        ("collection_selector_spec", SelectorKind.LIST, list_invoices),
        ("output_selector_spec", SelectorKind.RETRIEVE, invoice_by_pk),
    ],
)
def test_a_nested_selector_seat_declaring_permissions_is_refused_when_built(
    seat: str, kind: SelectorKind, selector: Any
) -> None:
    # 0.22.0: ``Unrestricted()`` is refused as any check is, and the message
    with pytest.raises(ImproperlyConfigured, match=seat):
        ServiceSpec(service=record_invoice, permissions=OPEN, **{seat: _seat(kind, selector)})


def test_a_precondition_naming_a_seed_nothing_fills_is_refused_at_registration() -> None:
    # 0.23.0: ``check_seeds`` reads a precondition's signature, so ``frozen``,
    def _check(*, frozen: bool) -> None:
        return None

    spec = ServiceSpec(service=record_invoice, permissions=OPEN, preconditions=[_check])
    server = MCPServer(name="t", auth_backend=HeaderAuthBackend())

    with pytest.raises(ImproperlyConfigured, match=r"preconditions\[0\].*frozen"):
        server.register_tool(spec, name="things.do")
