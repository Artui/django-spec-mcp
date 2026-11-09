"""``tools/list`` offers what a Validator declares to an MCP call, under the stamp alone.

``tools/call`` dispatches under a dict stamped ``mcp``, so a field a Validator
declares only there is one the call takes. The listing asks under the same stamp,
so a client following the schema can send it. It hands the stamp and no request,
even with a request in hand: a schema that varied by caller would make the shared
``cacheScope`` of a listing false, and a stamp varies by transport only.

Registration compiles the schema under the same stamp, for what it raises, so a
field that fails assembly under ``mcp`` alone fails the server's startup and not
its first listing.

Each test goes through ``register_tool`` or a binding built by hand, and reads the
listing the way a client does, so it runs unchanged against a tree without the fix.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, override_settings
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    ServiceSpec,
    Unrestricted,
    ValidationContext,
    Validator,
    call_transport,
)

from django_spec_mcp import MCP_TRANSPORT, MCPServer
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.conftest import post_jsonrpc
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import make_server

TOOL = "orders.place"


@dataclass
class _Placed:
    placed: bool


class _Validator(Validator):
    """``ref`` to anyone, and ``extra`` to the calls ``declares_extra`` says yes to."""

    def __init__(self, extra: Parameter | None = None) -> None:
        self.extra = extra
        self.handed: list[dict[str, Any] | None] = []

    def declares_extra(self, call_context: dict[str, Any] | None) -> bool:
        raise NotImplementedError

    def parameters(self, *, call_context: dict[str, Any] | None = None) -> Parameters:
        self.handed.append(call_context)
        declared = [Parameter("ref", "string")]
        if self.extra is not None and self.declares_extra(call_context):
            declared.append(self.extra)
        return Parameters(declared)

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        return dict(arguments)


class _ToOneTransport(_Validator):
    def __init__(self, transport: str, extra: Parameter) -> None:
        super().__init__(extra)
        self.transport = transport

    def declares_extra(self, call_context: dict[str, Any] | None) -> bool:
        return call_context is not None and call_transport(call_context) == self.transport


class _ToACaller(_Validator):
    """A declaration keyed on who is calling: never made to a listing."""

    def declares_extra(self, call_context: dict[str, Any] | None) -> bool:
        return call_context is not None and call_context["request"] is not None


class _GuardsNoneThenReadsTheRequest(_Validator):
    """The shape that passes while described with no context and now meets ``None``."""

    def declares_extra(self, call_context: dict[str, Any] | None) -> bool:
        return call_context is not None and call_context["request"].user.is_staff


def _place(**fields: Any) -> _Placed:
    return _Placed(placed=bool(fields))


def _spec(validator: Validator) -> ServiceSpec:
    return ServiceSpec(
        service=_place,
        validator=validator,
        presenter=DataclassPresenter(_Placed),
        permissions=[Unrestricted()],
    )


def _server(validator: Validator) -> MCPServer:
    server = make_server(session_store=InMemorySessionStore())
    server.register_tool(_spec(validator), name=TOOL, description="Place an order.")
    return server


def _on_the_wire(server: MCPServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """One request through the HTTP endpoint, the route with a request in hand."""
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        client = Client()
        initialized = post_jsonrpc(
            client,
            method="initialize",
            params={
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0.0"},
            },
            protocol_version=None,
        )
        response = post_jsonrpc(
            client,
            method=method,
            params=params,
            session_id=initialized["Mcp-Session-Id"],
        )
    assert response.status_code == 200, response.content
    return json.loads(response.content)["result"]


def _listed(server: MCPServer) -> dict[str, Any]:
    (tool,) = _on_the_wire(server, "tools/list", {})["tools"]
    return tool["inputSchema"]


def _to_mcp(name: str = "ref_mcp") -> _ToOneTransport:
    return _ToOneTransport(MCP_TRANSPORT, Parameter(name, "string", required=True))


def test_a_required_field_declared_only_under_mcp_is_listed() -> None:
    schema = _listed(_server(_to_mcp()))

    assert "ref_mcp" in schema["properties"]
    assert "ref_mcp" in schema["required"]


@pytest.mark.django_db
def test_a_call_sending_the_listed_field_succeeds() -> None:
    result = _on_the_wire(
        _server(_to_mcp()), "tools/call", {"name": TOOL, "arguments": {"ref_mcp": "A-1"}}
    )

    assert not result.get("isError"), result
    assert result["structuredContent"] == {"placed": True}


@pytest.mark.django_db
def test_a_call_leaving_the_listed_field_out_is_refused_for_it() -> None:
    # The listing and the call agree: what the one requires the other demands.
    result = _on_the_wire(_server(_to_mcp()), "tools/call", {"name": TOOL, "arguments": {}})

    assert result["isError"] is True
    assert "ref_mcp" in result["content"][0]["text"]


def test_the_in_process_listing_reads_the_same_path() -> None:
    listing: Any = _server(_to_mcp()).list_tools(user=None)
    (tool,) = listing["tools"]

    assert "ref_mcp" in tool["inputSchema"]["required"]


def test_a_field_declared_only_under_another_transport_is_not_listed() -> None:
    server = _server(_ToOneTransport("cli", Parameter("ref_cli", "string", required=True)))

    schema = _listed(server)

    assert list(schema["properties"]) == ["ref"]
    assert "required" not in schema


@pytest.mark.parametrize(
    "validator",
    [
        pytest.param(lambda: _to_mcp(), id="declared-under-mcp"),
        pytest.param(
            lambda: _ToOneTransport("cli", Parameter("ref_cli", "string")),
            id="declared-under-another-transport",
        ),
    ],
)
def test_a_listing_s_cache_scope_is_unchanged(validator: Any) -> None:
    """A stamp varies by transport only, so a listing keeps the scope of one with no such field."""
    listing = _on_the_wire(_server(validator()), "tools/list", {})
    plain = _on_the_wire(_server(_Validator()), "tools/list", {})

    assert plain["cacheScope"] == "public"
    assert listing["cacheScope"] == plain["cacheScope"]


def test_the_listing_hands_a_validator_the_stamp_and_no_request() -> None:
    """Read over HTTP, where the endpoint has a request to give and gives none."""
    validator = _to_mcp()
    server = _server(validator)
    validator.handed.clear()

    _listed(server)

    assert validator.handed
    for handed in validator.handed:
        assert handed is not None
        assert call_transport(handed) == MCP_TRANSPORT
        assert handed["request"] is None
        assert handed["action"] == TOOL


@pytest.mark.django_db
def test_a_field_declared_per_caller_stays_unoffered() -> None:
    # Offered only to a dict carrying a request, which a listing never hands. The
    # call has one, so the field is still taken there.
    server = _server(_ToACaller(Parameter("ref_caller", "string")))

    assert "ref_caller" not in _listed(server)["properties"]
    result = _on_the_wire(server, "tools/call", {"name": TOOL, "arguments": {"ref_caller": "A"}})
    assert not result.get("isError"), result


def _reads_the_request() -> _GuardsNoneThenReadsTheRequest:
    return _GuardsNoneThenReadsTheRequest(Parameter("ref_staff", "string"))


def test_a_validator_reading_the_none_request_raises_out_of_the_listing() -> None:
    # A binding built by hand reaches the listing past the compile ``register_tool``
    # makes. Its check of ``server_arguments`` reads the declaration made to no call,
    # which the guard answers. A description that raises is not caught here.
    server = make_server()
    server.tools.register(
        KernelToolBinding(name=TOOL, description="d", spec=_spec(_reads_the_request()))
    )

    with pytest.raises(AttributeError, match="user"):
        server.list_tools(user=None)


def test_a_validator_reading_the_none_request_raises_at_register_tool() -> None:
    server = make_server()

    with pytest.raises(AttributeError, match="user"):
        server.register_tool(_spec(_reads_the_request()), name=TOOL, description="d")
    assert TOOL not in server.tools


def test_a_field_failing_assembly_under_mcp_alone_raises_at_register_tool() -> None:
    server = make_server()

    with pytest.raises(ImproperlyConfigured, match="user"):
        server.register_tool(_spec(_to_mcp("user")), name=TOOL, description="d")
    assert TOOL not in server.tools
