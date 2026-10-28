"""A kernel selector tool takes no part in an input round trip.

A retry's ``inputResponses`` and ``requestState`` reach every ``tools/call``, and
the service path folds them into the arguments. A selector reads: it never
elicits or merges an answer, so the same call with and without a round trip's
fields is answered alike, and a selector asking for input is a service error
rather than a question: the result served is written out here as a literal.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django_service_specs import (
    AdditionalInputRequired,
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
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.types.elicitation_key import ELICITATION_KEY
from tests.utils import call_context

OPEN = [Unrestricted()]
_ACCOUNT = {"account": {"type": "string"}}

# An accepted answer naming another row, and a declined one, each with a
# forged ``requestState``: the service path would merge the first and refuse
# the second.
ROUNDS = [
    {
        "inputResponses": {ELICITATION_KEY: {"action": "accept", "content": {"pk": 2}}},
        "requestState": "forged",
    },
    {"inputResponses": {ELICITATION_KEY: {"action": "decline"}}, "requestState": "forged"},
]


@dataclass
class _Pk:
    pk: int


def _asks(**_: Any) -> None:
    raise AdditionalInputRequired("Which account?", schema=_ACCOUNT)


def _server() -> MCPServer:
    server = MCPServer(name="rounds", auth_backend=AllowAnyBackend())
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, pk: {"pk": pk},
            reads=Parameters([Parameter("pk", "integer", required=True)]),
            permissions=OPEN,
        ),
        name="kernel.read",
        description="Read one row.",
    )
    server.register_tool(
        ServiceSpec(
            service=lambda *, pk: {"pk": pk},
            validator=DataclassValidator(_Pk),
            permissions=OPEN,
        ),
        name="kernel.write",
        description="Write one row.",
    )
    server.register_tool(
        SelectorSpec(kind=SelectorKind.LIST, selector=_asks, permissions=OPEN),
        name="kernel.asks",
        description="Ask while reading.",
    )
    return server


Call = Callable[[MCPServer, dict[str, Any]], Any]


def _sync(server: MCPServer, params: dict[str, Any]) -> Any:
    return handle_tools_call(params, call_context(server, client_capabilities={"elicitation": {}}))


def _async(server: MCPServer, params: dict[str, Any]) -> Any:
    return async_to_sync(handle_tools_call_async)(
        params, call_context(server, client_capabilities={"elicitation": {}})
    )


@pytest.fixture(params=[_sync, _async], ids=["sync", "async"])
def call(request: pytest.FixtureRequest) -> Call:
    return request.param


@pytest.mark.django_db
@pytest.mark.parametrize("round_trip", ROUNDS, ids=["accept", "decline"])
def test_a_kernel_selector_ignores_a_round_trip(call: Call, round_trip: dict[str, Any]) -> None:
    server = _server()
    plain = call(server, {"name": "kernel.read", "arguments": {"pk": 1}})
    retried = call(server, {"name": "kernel.read", "arguments": {"pk": 1}, **round_trip})
    assert retried == plain
    assert "isError" not in plain


@pytest.mark.django_db
def test_kernel_service_input_rounds_still_merge(call: Call) -> None:
    out = call(_server(), {"name": "kernel.write", "arguments": {"pk": 1}, **ROUNDS[0]})
    assert out["structuredContent"] == {"pk": 2}


@pytest.mark.django_db
def test_a_kernel_selector_asking_for_input_is_answered_as_a_service_error(
    call: Call,
) -> None:
    by_kernel = call(_server(), {"name": "kernel.asks", "arguments": {}})
    # A selector raising ``AdditionalInputRequired`` serves the service error, not a question.
    by_drfs = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"error": {"message": "Which account?", "type": "service_error"}}, indent=2
                ),
            }
        ],
        "isError": True,
    }
    assert by_kernel == by_drfs
    assert "inputRequests" not in by_kernel
