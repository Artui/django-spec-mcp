from __future__ import annotations

import warnings
from typing import Any

import pytest
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django_service_specs import DataclassPresenter, SelectorKind, SelectorSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.testing import assert_tool_result_conforms
from tests.kernel_client import KernelClient
from tests.testapp.kernel_shapes import InvoiceOutput
from tests.testapp.models import Invoice
from tests.testapp.urlconf_for import urlconf_for

ENVELOPE = ["items", "page", "totalPages", "hasNext"]


def _list_spec() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=lambda: Invoice.objects.order_by("number"),
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[Unrestricted()],
    )


def _server(**knobs: Any) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        server.register_tool(_list_spec(), name="invoices.list", paginate=True, **knobs)
    return server


def _seed(count: int) -> None:
    for i in range(count):
        Invoice.objects.create(number=f"INV-{i}", amount_cents=100 + i, sent=False)


def _served(server: MCPServer, client: Client) -> tuple[dict[str, Any], dict[str, Any], int]:
    """The tool's ``tools/list`` entry, its ``tools/call`` result and the call's COUNTs."""
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        caller = KernelClient(client)
        caller.initialize()
        (tool,) = caller.post("tools/list", {}).json()["result"]["tools"]
        with CaptureQueriesContext(connection) as queries:
            body = caller.call("invoices.list", {"page": 1, "limit": 2})
    counts = sum("COUNT(" in query["sql"].upper() for query in queries.captured_queries)
    return tool, body["result"], counts


@pytest.mark.django_db
def test_the_server_wide_switch_sends_total_from_the_one_count(client: Client) -> None:
    _seed(5)
    with override_settings(SPEC_MCP={"INCLUDE_TOTAL": True}):
        server = _server()

    _tool, result, counts = _served(server, client)

    structured = result["structuredContent"]
    assert list(structured) == [*ENVELOPE, "total"]
    assert (structured["total"], structured["totalPages"], len(structured["items"])) == (5, 3, 2)
    assert counts == 1


@pytest.mark.django_db
def test_the_total_costs_no_query_the_page_did_not_already_make(client: Client) -> None:
    _seed(5)
    with CaptureQueriesContext(connection) as off:
        _served(_server(), client)
    with CaptureQueriesContext(connection) as on:
        _served(_server(include_total=True), client)

    assert len(on.captured_queries) == len(off.captured_queries)


@pytest.mark.django_db
def test_the_advertised_envelope_is_the_sent_one(client: Client) -> None:
    _seed(5)

    tool, result, _counts = _served(_server(include_total=True), client)

    schema = tool["outputSchema"]
    structured = result["structuredContent"]
    assert list(schema["properties"]) == list(structured) == [*ENVELOPE, "total"]
    assert schema["required"] == [*ENVELOPE, "total"]
    assert schema["properties"]["total"]["type"] == "integer"
    assert_tool_result_conforms(tool, result)


@pytest.mark.django_db
def test_off_the_envelope_is_unchanged_on_both_sides(client: Client) -> None:
    _seed(5)

    tool, result, _counts = _served(_server(), client)

    assert list(tool["outputSchema"]["properties"]) == ENVELOPE
    assert tool["outputSchema"]["required"] == ENVELOPE
    assert list(result["structuredContent"]) == ENVELOPE


@pytest.mark.django_db
def test_a_tool_turns_the_server_wide_switch_off(client: Client) -> None:
    _seed(5)
    with override_settings(SPEC_MCP={"INCLUDE_TOTAL": True}):
        server = _server(include_total=False)

    tool, result, _counts = _served(server, client)

    assert "total" not in tool["outputSchema"]["properties"]
    assert "total" not in result["structuredContent"]


@pytest.mark.django_db
def test_the_configured_switch_reaches_a_paged_list_and_leaves_an_unpaged_one(
    client: Client,
) -> None:
    _seed(1)
    server = MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=None,
        config=build_mcp_config(include_total=True),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        server.register_tool(_list_spec(), name="invoices.list")
        server.register_tool(_list_spec(), name="invoices.paged", paginate=True)
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        caller = KernelClient(client)
        caller.initialize()
        tools = {t["name"]: t for t in caller.post("tools/list", {}).json()["result"]["tools"]}
        unpaged = caller.call("invoices.list")["result"]["structuredContent"]
        paged = caller.call("invoices.paged")["result"]["structuredContent"]

    assert "total" not in tools["invoices.list"]["outputSchema"]["properties"]
    assert list(unpaged) == ["items"]
    assert "total" in tools["invoices.paged"]["outputSchema"]["required"]
    assert paged["total"] == 1
