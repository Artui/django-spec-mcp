"""A kernel selector tool takes no part in an input round trip, as a drf-services one does not.

A retry's ``inputResponses`` and ``requestState`` reach every ``tools/call``, and
the service path folds them into the arguments. A selector reads: drf-services
never lets one elicit or merge an answer, and the kernel path matches it, so
the same call with and without a round trip's fields is answered alike, and a
selector asking for input is a service error rather than a question.
"""

from __future__ import annotations

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
from rest_framework_services.types.selector_spec import SelectorSpec as DrfSelectorSpec

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
    server.register_selector_tool(
        name="drfs.read",
        spec=DrfSelectorSpec(kind=SelectorKind.LIST, selector=lambda: [{"pk": 1}]),
        description="Read the rows.",
    )
    server.register_selector_tool(
        name="drfs.asks",
        spec=DrfSelectorSpec(kind=SelectorKind.LIST, selector=_asks),
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
@pytest.mark.parametrize(("name", "arguments"), [("kernel.read", {"pk": 1}), ("drfs.read", {})])
def test_a_kernel_selector_ignores_a_round_trip(
    call: Call, round_trip: dict[str, Any], name: str, arguments: dict[str, Any]
) -> None:
    server = _server()
    plain = call(server, {"name": name, "arguments": arguments})
    retried = call(server, {"name": name, "arguments": arguments, **round_trip})
    assert retried == plain
    assert "isError" not in plain


@pytest.mark.django_db
def test_kernel_service_input_rounds_still_merge(call: Call) -> None:
    out = call(_server(), {"name": "kernel.write", "arguments": {"pk": 1}, **ROUNDS[0]})
    assert out["structuredContent"] == {"pk": 2}


@pytest.mark.django_db
def test_a_kernel_selector_asking_for_input_is_answered_as_a_drfs_selector_is(
    call: Call,
) -> None:
    server = _server()
    by_kernel = call(server, {"name": "kernel.asks", "arguments": {}})
    by_drfs = call(server, {"name": "drfs.asks", "arguments": {}})
    assert by_kernel == by_drfs
    assert by_kernel["isError"] is True
    assert "inputRequests" not in by_kernel
