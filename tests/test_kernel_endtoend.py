"""A kernel mount through the real JSON-RPC view, asserting the served payloads."""

from __future__ import annotations

import json

import pytest
from django.test import Client, override_settings

from tests.kernel_client import KernelClient
from tests.testapp.mcp import build_kernel_server
from tests.testapp.models import Invoice
from tests.testapp.urlconf_for import urlconf_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def kernel(client: Client):
    with override_settings(ROOT_URLCONF=urlconf_for(build_kernel_server())):
        caller = KernelClient(client)
        caller.initialize()
        yield caller


def test_initialize_lists_and_calls_through_the_kernel(kernel: KernelClient) -> None:
    listed = kernel.post("tools/list", {}).json()["result"]["tools"]
    by_name = {tool["name"]: tool for tool in listed}
    assert sorted(by_name) == ["invoices.create", "invoices.get", "invoices.list"]
    create = by_name["invoices.create"]
    # Spread arguments, as the kernel's schema has them: no ``data`` wrapper.
    assert create["inputSchema"] == {
        "type": "object",
        "properties": {
            "number": {"type": "string"},
            "amount_cents": {"type": "integer", "default": 0},
        },
        "required": ["number"],
        "additionalProperties": False,
    }
    assert create["annotations"] == {"readOnlyHint": False, "destructiveHint": True}
    assert by_name["invoices.get"]["annotations"] == {"readOnlyHint": True}
    assert by_name["invoices.list"]["outputSchema"]["required"] == [
        "items",
        "page",
        "totalPages",
        "hasNext",
    ]
    assert by_name["invoices.get"]["outputSchema"]["required"] == [
        "id",
        "number",
        "amount_cents",
        "sent",
    ]
    assert by_name["invoices.list"]["inputSchema"]["properties"]["page"] == {
        "type": "integer",
        "minimum": 1,
    }

    created = kernel.call("invoices.create", {"number": "INV-1", "amount_cents": 250})
    invoice = Invoice.objects.get()
    expected = {"id": invoice.pk, "number": "INV-1", "amount_cents": 250, "sent": False}
    assert created["result"]["structuredContent"] == expected
    assert json.loads(created["result"]["content"][0]["text"]) == expected
    assert "isError" not in created["result"]

    fetched = kernel.call("invoices.get", {"pk": invoice.pk})
    assert fetched["result"]["structuredContent"] == expected

    page = kernel.call("invoices.list", {"page": 1, "limit": 10})
    assert page["result"]["structuredContent"] == {
        "items": [expected],
        "page": 1,
        "totalPages": 1,
        "hasNext": False,
    }

    read = kernel.post("resources/read", {"uri": f"invoices://{invoice.pk}"}).json()
    contents = read["result"]["contents"]
    assert [(c["uri"], c["mimeType"]) for c in contents] == [
        (f"invoices://{invoice.pk}", "application/json")
    ]
    assert json.loads(contents[0]["text"]) == expected
