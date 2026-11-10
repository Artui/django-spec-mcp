from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from tests.utils import make_server

pytestmark = pytest.mark.django_db(transaction=True)

OPEN = [Unrestricted()]


@dataclass
class _Charge:
    number: str
    amount_cents: int = 250


def _read(*, number: str, limit: int = 5) -> dict[str, Any]:
    return {"number": number, "limit": limit}


def _charge(*, data: _Charge) -> dict[str, Any]:
    # The validated instance, not the caller's mapping: its fields are typed.
    return {"number": data.number, "amount_cents": data.amount_cents}


def _server() -> MCPServer:
    server = make_server(session_store=None)
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_read,
            reads=Parameters(
                [
                    Parameter("number", "string", required=True),
                    Parameter("limit", "integer", default=10),
                ]
            ),
            permissions=OPEN,
        ),
        name="read",
    )
    server.register_tool(
        ServiceSpec(service=_charge, validator=DataclassValidator(_Charge), permissions=OPEN),
        name="charge",
    )
    return server


@pytest.mark.parametrize(
    ("name", "arguments", "read"),
    [
        ("read", {"number": "A"}, {"number": "A", "limit": 5}),
        ("read", {"number": "A", "limit": "3"}, {"number": "A", "limit": 3}),
        ("charge", {"number": "A"}, {"number": "A", "amount_cents": 250}),
        ("charge", {"number": "A", "amount_cents": "3"}, {"number": "A", "amount_cents": 3}),
    ],
    ids=["read-selector-default", "read-coerced", "validator-default", "validator-coerced"],
)
async def test_the_callable_reads_the_declared_value(
    name: str, arguments: dict[str, Any], read: dict[str, Any]
) -> None:
    out = await _server().acall_tool(name, arguments, user=None)

    assert out["structuredContent"] == read


async def test_a_reads_default_is_reported_and_not_applied() -> None:
    # The read declares 10 over a selector defaulting to 5: the schema tells a
    server = _server()
    listed: Any = server.list_tools(user=None)
    schemas = {tool["name"]: tool["inputSchema"] for tool in listed["tools"]}

    out = await server.acall_tool("read", {"number": "A"}, user=None)

    assert schemas["read"]["properties"]["limit"]["default"] == 10
    assert out["structuredContent"]["limit"] == 5


def test_the_schema_requires_exactly_the_names_nothing_defaults() -> None:
    listed: Any = _server().list_tools(user=None)
    schemas = {tool["name"]: tool["inputSchema"] for tool in listed["tools"]}

    assert schemas["read"]["required"] == ["number"]
    assert schemas["charge"]["required"] == ["number"]
    assert schemas["read"]["properties"]["limit"]["default"] == 10
    assert set(schemas["charge"]["properties"]) == {"number", "amount_cents"}
    assert set(schemas["read"]["properties"]) == {"number", "limit"}


def _read_without_a_default(*, number: str, limit: int) -> dict[str, Any]:
    return {"number": number, "limit": limit}


def test_a_read_the_schema_would_offer_as_optional_over_a_required_keyword_is_refused() -> None:
    server = make_server(session_store=None)
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_read_without_a_default,
        reads=Parameters(
            [Parameter("number", "string", required=True), Parameter("limit", "integer")]
        ),
        permissions=OPEN,
    )

    with pytest.raises(
        ImproperlyConfigured, match=r"MCP tool 'read': .*\['limit'\], declared as optional reads"
    ):
        server.register_tool(spec, name="read")
    assert "read" not in server.tools
