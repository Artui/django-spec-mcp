from __future__ import annotations

from typing import Any

import pytest

from django_spec_mcp.protocol.parse_message import parse_message
from django_spec_mcp.protocol.types.client_capabilities import ClientCapabilities
from django_spec_mcp.protocol.types.completion import Completion
from django_spec_mcp.protocol.types.implementation import Implementation
from django_spec_mcp.protocol.types.initialize_params import InitializeParams
from django_spec_mcp.protocol.types.initialize_result import InitializeResult
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.json_rpc_notification import JsonRpcNotification
from django_spec_mcp.protocol.types.json_rpc_request import JsonRpcRequest
from django_spec_mcp.protocol.types.json_rpc_response import JsonRpcResponse
from django_spec_mcp.protocol.types.resource import Resource
from django_spec_mcp.protocol.types.resource_contents import ResourceContents
from django_spec_mcp.protocol.types.resource_template import ResourceTemplate
from django_spec_mcp.protocol.types.server_capabilities import ServerCapabilities
from django_spec_mcp.protocol.types.tool import Tool
from django_spec_mcp.protocol.types.tool_content_block import ToolContentBlock
from django_spec_mcp.protocol.types.tool_result import ToolResult
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.ui_extension_id import UI_EXTENSION_ID

_SERVER_INFO = Implementation(name="s", version="1")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(
            Implementation(name="x", version="1"),
            {"name": "x", "version": "1"},
            # ``title`` is optional per the spec; clients fall back to ``name``.
            id="implementation-omits-an-absent-title",
        ),
        pytest.param(
            Implementation(name="x", version="1", title="Nice Name"),
            {"name": "x", "version": "1", "title": "Nice Name"},
            id="implementation-carries-a-title",
        ),
        pytest.param(ClientCapabilities(), {}, id="client-capabilities-empty"),
        pytest.param(
            ClientCapabilities(
                roots={"list": True},
                sampling={},
                elicitation={"x": 1},
                experimental={"y": 2},
                extensions={UI_EXTENSION_ID: {}},
            ),
            {
                "roots": {"list": True},
                "sampling": {},
                "elicitation": {"x": 1},
                "experimental": {"y": 2},
                "extensions": {UI_EXTENSION_ID: {}},
            },
            id="client-capabilities-full",
        ),
        pytest.param(
            ServerCapabilities(
                tools={"list": {}},
                resources={"r": True},
                prompts={"p": True},
                completions={"c": 1},
                experimental={"e": 1},
            ),
            {
                "tools": {"list": {}},
                "resources": {"r": True},
                "prompts": {"p": True},
                "completions": {"c": 1},
                "experimental": {"e": 1},
            },
            id="server-capabilities-full",
        ),
        pytest.param(
            ServerCapabilities(tools={}, resources=None),
            {"tools": {}},
            id="server-capabilities-an-empty-capability-is-still-declared",
        ),
        pytest.param(
            ServerCapabilities(tools=None, resources={}),
            {"resources": {}},
            id="server-capabilities-a-none-capability-is-omitted",
        ),
        pytest.param(
            InitializeResult(
                protocol_version="2025-11-25",
                capabilities=ServerCapabilities(),
                server_info=_SERVER_INFO,
                instructions="hello",
            ),
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "serverInfo": {"name": "s", "version": "1"},
                "instructions": "hello",
            },
            id="initialize-result-with-instructions",
        ),
        pytest.param(
            InitializeResult(
                protocol_version="2025-11-25",
                capabilities=ServerCapabilities(),
                server_info=_SERVER_INFO,
            ),
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "serverInfo": {"name": "s", "version": "1"},
            },
            id="initialize-result-without-instructions",
        ),
        pytest.param(
            JsonRpcError(code=JsonRpcErrorCode.PARSE_ERROR, message="boom"),
            {"code": -32700, "message": "boom"},
            id="json-rpc-error-minimal",
        ),
        pytest.param(
            JsonRpcError(code=-32000, message="boom", data={"hint": "x"}),
            {"code": -32000, "message": "boom", "data": {"hint": "x"}},
            id="json-rpc-error-with-data",
        ),
        pytest.param(
            JsonRpcRequest(method="m", id=1, params={"a": 1}),
            {"jsonrpc": "2.0", "method": "m", "id": 1, "params": {"a": 1}},
            id="json-rpc-request-with-params",
        ),
        pytest.param(
            JsonRpcRequest(method="m", id=1),
            {"jsonrpc": "2.0", "method": "m", "id": 1},
            id="json-rpc-request-without-params",
        ),
        pytest.param(
            JsonRpcNotification(method="n", params={"x": 1}),
            {"jsonrpc": "2.0", "method": "n", "params": {"x": 1}},
            id="json-rpc-notification-with-params",
        ),
        pytest.param(
            JsonRpcNotification(method="n"),
            {"jsonrpc": "2.0", "method": "n"},
            id="json-rpc-notification-without-params",
        ),
        pytest.param(
            JsonRpcResponse(id=1, result={"ok": True}),
            # Every result carries the 2026-07-28 discriminator, stamped once here.
            {"jsonrpc": "2.0", "id": 1, "result": {"resultType": "complete", "ok": True}},
            id="json-rpc-response-result",
        ),
        pytest.param(
            JsonRpcResponse(id=1, error=JsonRpcError(code=-32600, message="bad")),
            {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "bad"}},
            id="json-rpc-response-error",
        ),
        pytest.param(
            Tool(
                name="t",
                description="d",
                title="T",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                annotations={"readOnlyHint": True},
            ),
            {
                "name": "t",
                "inputSchema": {"type": "object"},
                "title": "T",
                "description": "d",
                "outputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": True},
            },
            id="tool-full",
        ),
        pytest.param(
            Tool(name="t"), {"name": "t", "inputSchema": {"type": "object"}}, id="tool-minimal"
        ),
        pytest.param(
            ToolContentBlock(
                type="image", text="t", data="d", mime_type="image/png", annotations={"k": 1}
            ),
            {
                "type": "image",
                "text": "t",
                "data": "d",
                "mimeType": "image/png",
                "annotations": {"k": 1},
            },
            id="tool-content-block-full",
        ),
        pytest.param(
            ToolContentBlock(type="text"), {"type": "text"}, id="tool-content-block-minimal"
        ),
        pytest.param(
            ToolResult(content=[ToolContentBlock(type="text", text="oops")], is_error=True),
            {"content": [{"type": "text", "text": "oops"}], "isError": True},
            id="tool-result-with-error-flag",
        ),
        pytest.param(
            ToolResult(
                content=[ToolContentBlock(type="text", text="x")], structured_content={"a": 1}
            ),
            {"content": [{"type": "text", "text": "x"}], "structuredContent": {"a": 1}},
            id="tool-result-with-structured-content",
        ),
        pytest.param(
            Resource(
                uri="u://1",
                name="n",
                title="T",
                description="d",
                mime_type="text/plain",
                size=42,
                annotations={"k": 1},
            ),
            {
                "uri": "u://1",
                "name": "n",
                "title": "T",
                "description": "d",
                "mimeType": "text/plain",
                "size": 42,
                "annotations": {"k": 1},
            },
            id="resource-full",
        ),
        pytest.param(
            Resource(uri="u://1", name="n"), {"uri": "u://1", "name": "n"}, id="resource-minimal"
        ),
        pytest.param(
            ResourceTemplate(
                uri_template="u://{x}",
                name="n",
                title="T",
                description="d",
                mime_type="application/json",
                annotations={"k": 1},
            ),
            {
                "uriTemplate": "u://{x}",
                "name": "n",
                "title": "T",
                "description": "d",
                "mimeType": "application/json",
                "annotations": {"k": 1},
            },
            id="resource-template-full",
        ),
        pytest.param(
            ResourceTemplate(uri_template="u://", name="n"),
            {"uriTemplate": "u://", "name": "n"},
            id="resource-template-minimal",
        ),
        pytest.param(
            ResourceContents(uri="u", mime_type="text/plain", text="hi"),
            {"uri": "u", "mimeType": "text/plain", "text": "hi"},
            id="resource-contents-text",
        ),
        pytest.param(
            ResourceContents(uri="u", blob="ZGF0YQ=="),
            {"uri": "u", "blob": "ZGF0YQ=="},
            id="resource-contents-blob",
        ),
        pytest.param(ResourceContents(uri="u"), {"uri": "u"}, id="resource-contents-minimal"),
        pytest.param(
            # ``total`` is optional on the wire; the handler leaves it unset, but
            # a consumer building a ``Completion`` directly may know the count.
            Completion(values=("a",), total=42, has_more=True),
            {"values": ["a"], "total": 42, "hasMore": True},
            id="completion-emits-a-known-total",
        ),
    ],
)
def test_to_dict(value: Any, expected: dict[str, Any]) -> None:
    assert value.to_dict() == expected


def test_client_extensions_are_parsed_from_initialize() -> None:
    """How a client says it supports MCP Apps. Parsed for introspection —
    nothing gates on it, since advertisement is client→server only and the
    spec defines no server-side counterpart."""
    params = InitializeParams.from_payload(
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {"extensions": {UI_EXTENSION_ID: {}}},
            "clientInfo": {"name": "c", "version": "1"},
        }
    )

    assert params.capabilities.extensions == {UI_EXTENSION_ID: {}}


def test_a_client_advertising_no_extensions_parses_to_none() -> None:
    params = InitializeParams.from_payload({"protocolVersion": "2025-11-25"})
    assert params.capabilities.extensions is None


def test_initialize_params_from_payload_minimal() -> None:
    parsed = InitializeParams.from_payload({})
    assert parsed.protocol_version == ""
    assert parsed.client_info.name == ""


def test_initialize_params_from_payload_full() -> None:
    parsed = InitializeParams.from_payload(
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {
                "roots": {"list": True},
                "sampling": {},
                "elicitation": {},
                "experimental": {},
            },
            "clientInfo": {"name": "c", "version": "1"},
        }
    )
    assert parsed.protocol_version == "2025-11-25"
    assert parsed.capabilities.roots == {"list": True}
    assert parsed.client_info == Implementation(name="c", version="1")


def test_parse_message_request() -> None:
    msg = parse_message({"jsonrpc": "2.0", "id": 1, "method": "m", "params": {"x": 1}})
    assert isinstance(msg, JsonRpcRequest)
    assert msg.method == "m"


def test_parse_message_notification() -> None:
    msg = parse_message({"jsonrpc": "2.0", "method": "m"})
    assert isinstance(msg, JsonRpcNotification)


def test_parse_message_response_with_result() -> None:
    msg = parse_message({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    assert isinstance(msg, JsonRpcResponse)
    assert msg.result == {"ok": True}


def test_parse_message_response_with_error() -> None:
    msg = parse_message({"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "bad"}})
    assert isinstance(msg, JsonRpcResponse)
    assert msg.error is not None and msg.error.code == -32600


def test_parse_message_response_with_null_error() -> None:
    msg = parse_message({"jsonrpc": "2.0", "id": 1, "result": None, "error": None})
    assert isinstance(msg, JsonRpcResponse)
    assert msg.error is None


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        pytest.param([], "JSON object", id="not-a-dict"),
        pytest.param({"jsonrpc": "1.0", "method": "m"}, "version", id="wrong-version"),
        pytest.param({"jsonrpc": "2.0", "method": 7}, "must be a string", id="non-string-method"),
        pytest.param(
            {"jsonrpc": "2.0", "id": 1, "error": "boom"},
            "'error' must be an object",
            id="non-dict-error",
        ),
        pytest.param({"jsonrpc": "2.0", "id": 1}, "must contain", id="unknown-shape"),
    ],
)
def test_parse_message_rejects(payload: Any, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        parse_message(payload)
