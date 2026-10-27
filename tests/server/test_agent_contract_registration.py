"""A kernel spec's audience markings reach the tool through every registrar.

What a caller with no HTTP request has to be told about a result (which field
names the record, which is a handle, which it never sees) is declared once, in
the presenter's dataclass, as ``Annotated`` markings (``AgentInvoiceOutput``, the
kernel twin of ``AgentInvoiceSerializer``). These cases check that the mount
reads it through each public entry point, ``register_tool`` and a
``register_tools`` entry, as far as the served payload, because a shared declaration nobody reads is worse than none.

The drf-services original asked this of an entry's ``OfflineContract`` and is
kept beside this file as ``test_agent_contract_registration_drfs.py``. Its
``url_kwargs`` and ``query_params`` have no kernel counterpart, since a kernel
spec declares its route and query values as ``reads``, which the schema lane
asks; nor does overriding the contract per mount, since ``field_audiences`` is
not a knob a kernel tool keeps. Two labels raising naming the tool is asked at
registration in ``test_registration_compiles_the_spec.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from django_service_specs import (
    DataclassPresenter,
    FieldAudience,
    RegisteredSpec,
    SelectorKind,
    SelectorSpec,
    SpecRegistry,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from tests.testapp.kernel_shapes import AgentInvoiceOutput


def _invoices() -> list[AgentInvoiceOutput]:
    return [AgentInvoiceOutput(id=7, number="INV-7", amount_cents=100, sent=True)]


def _spec() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_invoices,
        presenter=DataclassPresenter(AgentInvoiceOutput),
        permissions=[Unrestricted()],
    )


def _direct() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(_spec(), name="invoices", description="d", paginate=True)
    return server


def _bulk() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tools(
        SpecRegistry([RegisteredSpec(name="invoices", spec=_spec())]),
        overrides={"invoices": {"description": "d", "paginate": True}},
    )
    return server


registrars = pytest.mark.parametrize("build", [_direct, _bulk], ids=["register_tool", "bulk"])


@registrars
def test_the_binding_reads_the_presenters_markings(build: Any) -> None:
    projection = build().tools.get("invoices").audience_projection
    assert projection.label == "number"
    assert projection.audience("id") is FieldAudience.HANDLE
    assert projection.audience("sent") is FieldAudience.HIDDEN


@registrars
def test_the_served_payload_reads_them_too(build: Any) -> None:
    result = build().call_tool("invoices", {}, user=None)
    (item,) = result.structured_content["items"]
    assert "sent" not in item
    assert item["number"] == "INV-7"
