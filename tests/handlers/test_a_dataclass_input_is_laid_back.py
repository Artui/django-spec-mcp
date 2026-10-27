"""What a kernel tool's input declaration validates is what its callable reads.

The drf-services original asked this of a selector tool's MCP-only
``input_serializer`` shaped as a ``@dataclass`` or a ``DataclassSerializer``,
whose validated values were not laid back over the selector's arguments, so it
coerced and defaulted for nothing, and of registration and the schema, which
each counted a different set of names as filled. A kernel selector has no such
serializer: its arguments are its declared ``reads``, each with its own type,
and a service's are its Validator's fields. So the question is asked of those
two: the value the callable reads is the coerced one, or the default where the
call left the argument out, and the schema requires exactly the names nothing
defaults. A read's declared ``default`` is reported in the schema, not applied,
so the default a selector reads is its own signature's, declared beside it.
A read declared optional over a selector with no default of its own is where
the two come apart: the schema offers it as optional and the call leaving it
out is refused. Held as a strict ``xfail``, as a question for the kernel
rather than a settled defect. The original is kept beside this file as
``test_a_dataclass_input_is_laid_back_drfs.py``.
"""

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
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend

pytestmark = pytest.mark.django_db(transaction=True)

OPEN = [Unrestricted()]


@dataclass
class _Charge:
    number: str
    amount_cents: int = 250


def _read(*, number: str, limit: int = 5) -> dict[str, Any]:
    # Its own default differs from the 10 the read declares, so a test sees which
    # of the two a call applies.
    return {"number": number, "limit": limit}


def _charge(*, data: _Charge) -> dict[str, Any]:
    # The validated instance, not the caller's mapping: its fields are typed.
    return {"number": data.number, "amount_cents": data.amount_cents}


def _server() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
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
    # client 10, and a call leaving the argument out reads the selector's 5.
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "A read declared optional over a selector keyword with no default is offered as "
        "optional and refused when left out. Fixed only if registration refuses that "
        "declaration, which is a question for the kernel, not a settled defect: its "
        "selectors/utils.py refuses such a keyword at the call because a registered seed "
        "may fill it, held by test_an_optional_read_a_selector_requires_is_refused_when_not_sent."
    ),
)
async def test_a_read_the_schema_offers_as_optional_is_not_refused_when_left_out() -> None:
    # A registration refusing the declaration is the two agreeing too, since no
    # client is ever shown the schema.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    try:
        server.register_tool(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_read_without_a_default,
                reads=Parameters(
                    [Parameter("number", "string", required=True), Parameter("limit", "integer")]
                ),
                permissions=OPEN,
            ),
            name="read",
        )
    except ImproperlyConfigured:
        return
    listed: Any = server.list_tools(user=None)

    out = await server.acall_tool("read", {"number": "A"}, user=None)

    assert "limit" not in listed["tools"][0]["inputSchema"]["required"]
    assert "isError" not in out, out
