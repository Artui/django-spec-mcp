"""What the kernel floor in ``pyproject.toml`` rests on.

The floor, ``django-service-specs>=0.23.0``, is a chosen one: the package imports
on 0.20.0 and the rest of the suite passes on 0.22.0, so nothing else here fails
below it. These two behaviours are the 0.22.0 and 0.23.0 ones the docs and the
changelog tell a project to rely on, and each is asserted here so that a floor
lowered past its release fails a test rather than holding by luck.
"""

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
    # names the seat, so the loosest match that still tells the seats apart.
    with pytest.raises(ImproperlyConfigured, match=seat):
        ServiceSpec(service=record_invoice, permissions=OPEN, **{seat: _seat(kind, selector)})


def test_a_precondition_naming_a_seed_nothing_fills_is_refused_at_registration() -> None:
    # 0.23.0: ``check_seeds`` reads a precondition's signature, so ``frozen``,
    # which no registered seed fills, is refused when the tool registers. On
    # 0.22.0 the spec registered and failed every call.
    def _check(*, frozen: bool) -> None:
        return None

    spec = ServiceSpec(service=record_invoice, permissions=OPEN, preconditions=[_check])
    server = MCPServer(name="t", auth_backend=HeaderAuthBackend())

    with pytest.raises(ImproperlyConfigured, match=r"preconditions\[0\].*frozen"):
        server.register_tool(spec, name="things.do")
