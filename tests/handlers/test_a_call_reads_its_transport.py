"""Every ``call_context`` this package builds says the call came over MCP.

A project's own callable reads ``call_transport(call_context)`` to tell a call
over MCP from one over any other transport. So the value has to hold for every
reader of one call, on every route that builds a context, and for a call proxied
in process, which ``acall_tool`` is: the entry spec-agents' bridge reaches.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.test import RequestFactory
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    TRANSPORT_KEY,
    Affordance,
    Parameter,
    Parameters,
    PermissionCheck,
    PresentationContext,
    Presenter,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
    ValidationContext,
    Validator,
    call_transport,
)

import django_spec_mcp
from django_spec_mcp import MCP_TRANSPORT, SubscriptionFilter
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.handle_completion_complete import handle_completion_complete
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.handle_tools_list import handle_tools_list
from django_spec_mcp.handlers.kernel_utils import call_context as build_context
from django_spec_mcp.subscriptions.grant_subscription import grant_subscription
from tests.utils import call_context, make_server

TOOL = "orders.place"


class _Readers:
    """One of each reader a call has: what each saw of the transport, in order."""

    def __init__(self) -> None:
        self.seen: dict[str, list[str | None]] = {}

    def saw(self, reader: str, handed: Mapping[str, Any] | None) -> None:
        assert handed is not None, f"{reader} was handed no call_context"
        self.seen.setdefault(reader, []).append(call_transport(handed))


class _Check(PermissionCheck):
    def __init__(self, readers: _Readers) -> None:
        self.readers = readers

    def has_permission(self, principal: Any, spec: Any, *, call_context: Any) -> bool:
        self.readers.saw("check", call_context)
        return True


class _Validator(Validator):
    def __init__(self, readers: _Readers) -> None:
        self.readers = readers

    def parameters(self, *, call_context: dict[str, Any] | None = None) -> Parameters:
        return Parameters.of(Parameter("pk", "any", required=False))

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        self.readers.saw("validator", context.call_context)
        return dict(arguments)


class _Presenter(Presenter):
    def __init__(self, readers: _Readers) -> None:
        self.readers = readers

    def output(self, *, call_context: dict[str, Any] | None = None) -> Any:
        return None

    def present(self, value: Any) -> Any:
        return value

    def presented(self, value: Any, context: PresentationContext) -> Any:
        self.readers.saw("presenter", context.call_context)
        return value


def _server() -> tuple[Any, _Readers]:
    readers = _Readers()

    def place(*, call_context: dict[str, Any], **_: Any) -> dict[str, Any]:
        readers.saw("service", call_context)
        return {"placed": True}

    server = make_server()
    server.register_tool(
        ServiceSpec(
            service=place,
            validator=_Validator(readers),
            presenter=_Presenter(readers),
            permissions=[_Check(readers)],
            atomic=False,
        ),
        name=TOOL,
    )
    return server, readers


def _every_reader_read_mcp(readers: _Readers) -> None:
    """Each of the four was asked, and none read anything but ``mcp``."""
    assert set(readers.seen) == {"check", "validator", "service", "presenter"}, readers.seen
    assert {value for values in readers.seen.values() for value in values} == {"mcp"}


def test_the_constant_is_the_plain_string_the_key_holds() -> None:
    """A plain string, exported from the root, so a project compares against it
    without importing a type (and a kernel lists no transports)."""
    assert MCP_TRANSPORT == "mcp"
    assert type(MCP_TRANSPORT) is str
    assert "MCP_TRANSPORT" in django_spec_mcp.__all__


def test_the_builder_stamps_the_key_beside_the_standard_entries() -> None:
    request = object()
    built = build_context(request, "a.tool")
    assert built == {
        "request": request,
        "view": None,
        "format": None,
        "action": "a.tool",
        TRANSPORT_KEY: "mcp",
    }


def test_each_call_gets_a_dict_of_its_own() -> None:
    """Built fresh, so a key one spec writes reaches no other call."""
    assert build_context(None, "a") is not build_context(None, "a")


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_every_reader_of_a_wire_call_reads_one_value(is_async: bool) -> None:
    server, readers = _server()
    params = {"name": TOOL, "arguments": {}}
    if is_async:
        out = await handle_tools_call_async(params, call_context(server))
    else:
        out = await sync_to_async(handle_tools_call)(params, call_context(server))
    assert out["structuredContent"] == {"placed": True}, out
    _every_reader_read_mcp(readers)


def test_every_reader_of_an_in_process_call_tool_reads_one_value() -> None:
    server, readers = _server()
    result = server.call_tool(TOOL, {}, user=None)
    assert result.structured_content == {"placed": True}
    _every_reader_read_mcp(readers)


async def test_a_call_proxied_through_acall_tool_reads_mcp() -> None:
    """The call spec-agents' MCP bridge makes (``integrations/mcp.py``): positional
    name and arguments, the run's user, and the run's own request handed on. This
    package cannot depend on spec-agents, so the line is held at the entry it
    reaches. The transport a call came in on is the one that builds its dict, so
    a proxied call reads ``mcp``, whoever proxies it."""
    server, readers = _server()
    request = RequestFactory().post("/agent/")
    out = await server.acall_tool(TOOL, {}, user=None, request=request)
    assert out["structuredContent"] == {"placed": True}, out
    _every_reader_read_mcp(readers)


def test_a_listing_asks_the_checks_with_the_stamp() -> None:
    """A listing filtered by permission asks the check, under the same stamp."""
    server, readers = _server()
    config = replace(build_mcp_config(), filter_listings_by_permissions=True)
    out = handle_tools_list(None, call_context(server, config=config))
    assert [tool["name"] for tool in out["tools"]] == [TOOL]
    assert readers.seen == {"check": ["mcp"]}


def _resource_server() -> tuple[Any, _Readers]:
    readers = _Readers()

    def read(*, pk: int, call_context: dict[str, Any]) -> dict[str, Any]:
        readers.saw("selector", call_context)
        return {"pk": pk}

    server = make_server()
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=read,
            reads=Parameters([Parameter("pk", "integer", required=True)]),
            permissions=[_Check(readers)],
        ),
        name="order",
        uri_template="orders://{pk}",
        completions={"pk": lambda: ["1"]},
    )
    return server, readers


def test_a_resource_read_reads_the_stamp() -> None:
    server, readers = _resource_server()
    out = handle_resources_read({"uri": "orders://1"}, call_context(server))
    assert "contents" in out, out
    assert readers.seen == {"check": ["mcp"], "selector": ["mcp"]}


def test_a_completion_asks_the_checks_with_the_stamp() -> None:
    server, readers = _resource_server()
    params = {
        "ref": {"type": "ref/resource", "uri": "orders://{pk}"},
        "argument": {"name": "pk", "value": ""},
    }
    out = handle_completion_complete(params, call_context(server))
    assert out["completion"]["values"] == ["1"], out
    assert readers.seen == {"check": ["mcp"]}


def test_a_watch_asks_the_checks_with_the_stamp() -> None:
    server, readers = _resource_server()
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("orders://1",)), call_context(server)
    )
    assert granted.resource_uris == ("orders://1",)
    assert readers.seen["check"] == ["mcp"]


def test_a_conditioned_tool_s_seeds_read_the_stamp_in_a_listing() -> None:
    read: list[str | None] = []

    def transport(*, call_context: Any) -> str | None:
        read.append(call_transport(call_context))
        return call_transport(call_context)

    def available(transport: str | None) -> bool:
        return transport == MCP_TRANSPORT

    server = make_server(pool_seeds=DEFAULT_POOL_SEEDS.extend(transport=transport))
    server.register_tool(
        ServiceSpec(
            service=lambda: None,
            permissions=[Unrestricted()],
            affordances=[Affordance(code="c", reason="r", when=available)],
        ),
        name=TOOL,
    )
    context = call_context(server, pool_seeds=server.pool_seeds)
    listed: Any = handle_tools_list(None, context)
    assert [tool["name"] for tool in listed["tools"]] == [TOOL]
    assert read == ["mcp"]
