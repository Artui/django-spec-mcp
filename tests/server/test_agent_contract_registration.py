from __future__ import annotations

from typing import Any

import pytest
from django_service_specs import (
    DataclassPresenter,
    FieldAudience,
    RegisteredSpec,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
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


def _service_spec() -> ServiceSpec:
    return ServiceSpec(
        service=lambda: AgentInvoiceOutput(id=7, number="INV-7", amount_cents=100, sent=True),
        presenter=DataclassPresenter(AgentInvoiceOutput),
        permissions=[Unrestricted()],
    )


def _service_direct() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(_service_spec(), name="issue", description="d")
    return server


def _service_bulk() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tools(
        SpecRegistry([RegisteredSpec(name="issue", spec=_service_spec())]),
        overrides={"issue": {"description": "d"}},
    )
    return server


service_registrars = pytest.mark.parametrize(
    "build", [_service_direct, _service_bulk], ids=["register_tool", "bulk"]
)


@service_registrars
@pytest.mark.django_db
def test_a_service_tool_reads_the_presenters_markings(build: Any) -> None:
    server = build()
    projection = server.tools.get("issue").audience_projection
    assert projection.audience("sent") is FieldAudience.HIDDEN
    result = server.call_tool("issue", {}, user=None)
    assert "sent" not in result.structured_content
    assert result.structured_content["number"] == "INV-7"
