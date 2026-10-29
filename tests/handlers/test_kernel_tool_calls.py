"""The kernel path's calls, listing and reads beyond the end-to-end walk."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Any

import pytest
from django.test import Client, override_settings
from django_service_specs import (
    AdditionalInputRequired,
    DataclassValidator,
    NotPermitted,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceError,
    ServiceSpec,
    UnknownArguments,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.kernel_refusals import KERNEL_REFUSALS
from django_spec_mcp.handlers.types.kernel_refusal_scene import KernelRefusalScene
from django_spec_mcp.protocol.types.tool_result import ToolResult
from django_spec_mcp.schema.types.agent_conventions import AgentConventions
from django_spec_mcp.types.tool_content_kind import ToolContentKind
from tests.kernel_client import KernelClient
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.mcp import build_kernel_server
from tests.testapp.models import Invoice
from tests.testapp.urlconf_for import urlconf_for

pytestmark = pytest.mark.django_db(transaction=True)

OPEN = [Unrestricted()]


@dataclass
class _Tenanted:
    number: str
    tenant: str


class _Deny(PermissionCheck):
    def has_permission(self, *args: Any, **kwargs: Any) -> bool:
        return False


def _scopes_seen(*, request: Any) -> dict[str, Any]:
    return {"scopes": list(request.mcp_token.scopes), "action": "seen"}


def _rows() -> list[dict[str, int]]:
    return [{"n": n} for n in range(5)]


def _closed() -> None:
    raise ServiceError("closed")


def _build() -> MCPServer:
    server = build_kernel_server()
    server.register_tool(
        ServiceSpec(service=lambda: {"ok": True}, permissions=OPEN),
        name="gated",
        scopes=("invoices:write",),
    )
    server.register_tool(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_scopes_seen, permissions=OPEN),
        name="whoami",
    )
    server.register_tool(
        SelectorSpec(kind=SelectorKind.LIST, selector=_rows, permissions=OPEN),
        name="rows",
        paginate=True,
        max_page_size=2,
    )
    server.register_tool(
        ServiceSpec(
            service=lambda *, number, tenant: {"tenant": tenant},
            validator=DataclassValidator(_Tenanted),
            permissions=OPEN,
        ),
        name="tenanted",
        server_arguments=ServerArguments(values={"tenant": "acme"}),
    )
    server.register_tool(
        ServiceSpec(service=lambda: None, permissions=OPEN),
        name="pixel",
        content_kind=ToolContentKind.IMAGE,
        content_mime_type="image/png",
    )
    server.register_tool(
        ServiceSpec(service=lambda: None, permissions=OPEN),
        name="quiet",
        include_output_schema=False,
    )
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=lambda: {"secret": 1}, permissions=[_Deny()]
        ),
        name="vault",
        uri_template="vault://",
    )
    server.register_resource(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_closed, permissions=OPEN),
        name="ledger",
        uri_template="ledger://",
    )
    server.register_resource(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: {"ok": True}, permissions=OPEN),
        name="scoped",
        uri_template="scoped://",
        scopes=("read",),
    )
    return server


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def mount(request: pytest.FixtureRequest) -> Iterator[None]:
    with override_settings(ROOT_URLCONF=urlconf_for(_build(), is_async=request.param)):
        yield


def _caller(client: Client, **kwargs: Any) -> KernelClient:
    caller = KernelClient(client, **kwargs)
    caller.initialize()
    return caller


def test_a_missing_scope_is_insufficient_permission(mount: None, client: Client) -> None:
    body = _caller(client, scopes="other").call("gated")
    assert body["error"]["code"] == -32006
    assert body["error"]["data"] == {"requiredScopes": ["invoices:write"]}
    assert _caller(client, scopes="invoices:write").call("gated")["result"][
        "structuredContent"
    ] == {"ok": True}


def test_the_spec_reads_the_token_off_its_request(mount: None, client: Client) -> None:
    body = _caller(client, scopes="a b").call("whoami")
    assert body["result"]["structuredContent"] == {"scopes": ["a", "b"], "action": "seen"}


@pytest.mark.parametrize(
    ("arguments", "page", "items"),
    [
        ({"page": 2, "limit": 2}, 2, [{"n": 2}, {"n": 3}]),
        ({"page": "3", "limit": "2"}, 3, [{"n": 4}]),
        ({"page": True, "limit": "lots"}, 1, [{"n": 0}, {"n": 1}]),
        ({"page": None}, 1, [{"n": 0}, {"n": 1}]),
        # ``True`` is an ``int``: read as one, it would be a limit of 1 and
        # five pages.
        ({"page": 2, "limit": True}, 2, [{"n": 2}, {"n": 3}]),
    ],
    ids=["ints", "numeric-strings", "unreadable", "absent", "bool-limit"],
)
def test_a_paginated_tool_serves_one_clamped_page(
    mount: None, client: Client, arguments: dict[str, Any], page: int, items: list[Any]
) -> None:
    served = _caller(client).call("rows", arguments)["result"]["structuredContent"]
    assert (served["page"], served["items"], served["totalPages"]) == (page, items, 3)


def test_the_listing_reads_the_binding(mount: None, client: Client) -> None:
    tools = {t["name"]: t for t in _caller(client).post("tools/list", {}).json()["result"]["tools"]}
    assert tools["rows"]["inputSchema"]["properties"]["limit"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 2,
    }
    # The server answers for ``tenant``, so no caller is asked for it.
    assert set(tools["tenanted"]["inputSchema"]["properties"]) == {"number"}
    assert tools["tenanted"]["inputSchema"]["required"] == ["number"]
    assert "outputSchema" not in tools["pixel"]
    assert "outputSchema" not in tools["quiet"]


def test_a_server_argument_reaches_the_service(mount: None, client: Client) -> None:
    body = _caller(client).call("tenanted", {"number": "1", "tenant": "evil"})
    assert body["result"]["structuredContent"] == {"tenant": "acme"}


def test_a_callers_output_format_is_not_read(mount: None, client: Client) -> None:
    """Over the wire as in the handler: the text block is JSON whatever is asked for,
    and a format the server never knew is not refused."""
    caller = _caller(client)
    body = caller.post(
        "tools/call", {"name": "invoices.list", "arguments": {}, "outputFormat": "xml"}
    ).json()
    assert body["result"]["content"] == [
        {
            "type": "text",
            "text": '{\n  "hasNext": false,\n  "items": [],\n  "page": 1,\n  "totalPages": 1\n}',
        }
    ]


def test_a_denied_resource_read_is_insufficient_permission(mount: None, client: Client) -> None:
    body = _caller(client).post("resources/read", {"uri": "vault://"}).json()
    assert body["error"] == {"code": -32006, "message": "Insufficient permission"}


def test_a_missing_scope_on_a_resource_is_insufficient_permission(
    mount: None, client: Client
) -> None:
    denied = _caller(client).post("resources/read", {"uri": "scoped://"}).json()
    assert denied["error"]["code"] == -32006
    read = _caller(client, scopes="read").post("resources/read", {"uri": "scoped://"}).json()
    assert json.loads(read["result"]["contents"][0]["text"]) == {"ok": True}


def test_a_missing_resource_row_is_resource_not_found(mount: None, client: Client) -> None:
    body = _caller(client).post("resources/read", {"uri": "invoices://999"}).json()
    assert body["error"]["data"] == {"uri": "invoices://999"}
    assert body["error"]["message"] == "Resource not found: 'invoices://999'"


def test_a_resource_service_error_is_an_internal_error(mount: None, client: Client) -> None:
    body = _caller(client).post("resources/read", {"uri": "ledger://"}).json()
    assert body["error"]["code"] == -32603


def test_call_tool_runs_a_kernel_tool_in_process() -> None:
    server = _build()
    created = server.call_tool("invoices.create", {"number": "INV-9"}, user=None)
    assert isinstance(created, ToolResult)
    assert created.structured_content["number"] == "INV-9"
    assert server.call_tool("rows", {"page": 3, "limit": 2}, user=None).structured_content[
        "items"
    ] == [{"n": 4}]
    missing = server.call_tool("invoices.get", {"pk": 999}, user=None)
    assert missing.is_error is True
    refused = server.call_tool("invoices.create", {}, user=None)
    assert refused.is_error is True
    assert Invoice.objects.count() == 1


def test_call_tool_raises_a_refusal_the_wire_answers_with_a_json_rpc_error() -> None:
    server = _build()
    server.register_tool(ServiceSpec(service=lambda: None, permissions=[_Deny()]), name="denied")
    with pytest.raises(NotPermitted):
        server.call_tool("denied", {}, user=None)


class _Span:
    def __init__(self) -> None:
        self.recorded: list[BaseException] = []

    def record_exception(self, exc: BaseException) -> None:
        self.recorded.append(exc)


def _scene(*, record: bool, span: Any) -> KernelRefusalScene:
    return KernelRefusalScene(
        arguments={},
        config=replace(build_mcp_config(), record_service_exceptions=record),
        conventions=AgentConventions(),
        span=span,
    )


@pytest.mark.parametrize("record", [True, False])
def test_a_service_error_is_recorded_only_when_asked(record: bool) -> None:
    span = _Span()
    exc = ServiceError("closed")
    answer = KERNEL_REFUSALS[ServiceError](exc, _scene(record=record, span=span))
    assert isinstance(answer, ToolResult) and answer.is_error is True
    assert span.recorded == ([exc] if record else [])


def test_a_service_error_with_no_span_is_answered_without_one() -> None:
    answer = KERNEL_REFUSALS[ServiceError](ServiceError("closed"), _scene(record=True, span=None))
    assert isinstance(answer, ToolResult) and answer.is_error is True


def test_call_tool_answers_a_request_for_input_as_a_service_error() -> None:
    server = _build()
    server.register_tool(ServiceSpec(service=_asks, permissions=OPEN), name="asks")
    result = server.call_tool("asks", {}, user=None)
    assert json.loads(result.content[0].text)["error"] == {
        "type": "service_error",
        "message": "Which account?",
    }


def _asks() -> None:
    raise AdditionalInputRequired("Which account?", schema={"account": {"type": "string"}})


def test_with_no_page_ceiling_the_limit_has_no_maximum(client: Client) -> None:
    server = MCPServer(
        name="t",
        auth_backend=HeaderAuthBackend(),
        config=replace(build_mcp_config(), max_page_size=None),
    )
    server.register_tool(
        SelectorSpec(kind=SelectorKind.LIST, selector=_rows, permissions=OPEN),
        name="rows",
        paginate=True,
    )
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        (tool,) = _caller(client).post("tools/list", {}).json()["result"]["tools"]
    assert tool["inputSchema"]["properties"]["limit"] == {"type": "integer", "minimum": 1}


def test_an_oversized_resource_read_is_refused(client: Client) -> None:
    server = build_kernel_server(config=replace(build_mcp_config(), max_result_bytes=10))
    Invoice.objects.create(number="INV-1")
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        pk = Invoice.objects.get().pk
        body = _caller(client).post("resources/read", {"uri": f"invoices://{pk}"}).json()
    assert body["error"]["code"] == -32000


@dataclass
class _Numbered:
    number: str


@pytest.mark.parametrize("policy", [UnknownArguments.IGNORE, UnknownArguments.REJECT])
def test_unknown_arguments_reaches_the_schema_and_the_call(
    policy: UnknownArguments, client: Client
) -> None:
    server = MCPServer(name="t", auth_backend=HeaderAuthBackend())
    server.register_tool(
        ServiceSpec(
            service=lambda *, number: {"number": number},
            validator=DataclassValidator(_Numbered),
            permissions=OPEN,
        ),
        name="numbered",
        unknown_arguments=policy,
    )
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        caller = _caller(client)
        (tool,) = caller.post("tools/list", {}).json()["result"]["tools"]
        result = caller.call("numbered", {"number": "1", "extra": 2})["result"]
    closed = policy is UnknownArguments.REJECT
    assert (tool["inputSchema"].get("additionalProperties") is False) is closed
    assert result.get("isError", False) is closed
    if not closed:
        assert result["structuredContent"] == {"number": "1"}


def test_required_scopes_lists_every_declared_scope(client: Client) -> None:
    """``requiredScopes`` is the gate's whole declaration, not what the token
    lacks, as on a prompt's or a view's scope gate: a token carrying one of two
    scopes is told both."""
    server = MCPServer(name="t", auth_backend=HeaderAuthBackend())
    server.register_tool(
        ServiceSpec(service=lambda: None, permissions=OPEN),
        name="pair",
        description="Needs both scopes.",
        scopes=("read", "write"),
    )
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        body = _caller(client, scopes="read").call("pair")
    assert body["error"]["data"] == {"requiredScopes": ["read", "write"]}


def test_a_kernel_resource_serves_its_meta(client: Client) -> None:
    server = MCPServer(name="t", auth_backend=HeaderAuthBackend())
    server.register_resource(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: {"ok": 1}, permissions=OPEN),
        name="flagged",
        uri_template="flagged://",
        meta={"example.com/tier": "gold"},
    )
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        read = _caller(client).post("resources/read", {"uri": "flagged://"}).json()
    (contents,) = read["result"]["contents"]
    assert contents["_meta"] == {"example.com/tier": "gold"}
