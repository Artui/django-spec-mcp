from __future__ import annotations

import asyncio
import base64
import io
import json
from typing import Any

import pytest
from django.test import AsyncClient, Client, override_settings
from django_service_specs import (
    FilePresenter,
    FileResult,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer, ToolContentKind
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.output.tool_result import build_tool_result
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import LEGACY, MODERN, _send, make_server, post_in_era

pytestmark = pytest.mark.django_db

OPEN = [Unrestricted()]
PDF_BYTES = b"%PDF-1.7\n" + bytes(range(256))
PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x01"
CEILING = 1024


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class _Opened:
    """Holds the file objects a service handed out, so a test can ask whether each was closed."""

    def __init__(self) -> None:
        self.files: list[io.BytesIO] = []

    def __call__(self, data: bytes) -> io.BytesIO:
        self.files.append(io.BytesIO(data))
        return self.files[-1]


def _server(**overrides: Any) -> MCPServer:
    return make_server(**overrides)


def _call(server: MCPServer, is_async: bool, name: str, era: str = LEGACY) -> Any:
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        return post_in_era(era, is_async, "tools/call", {"name": name, "arguments": {}})


def _read(server: MCPServer, is_async: bool, uri: str) -> Any:
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        return post_in_era(LEGACY, is_async, "resources/read", {"uri": uri})


def _tool(server: MCPServer, name: str, service: Any, presenter: FilePresenter, **knobs: Any):
    server.register_tool(
        ServiceSpec(
            service=service, permissions=OPEN, presenter=presenter, **knobs.pop("spec", {})
        ),
        name=name,
        description="A file.",
        **knobs,
    )


_SYNC_AND_ASYNC = pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])


# ----- a resource over a file reads as a base64 blob of its type -----


def _invoice_pdf(*, pk: int) -> bytes:
    return PDF_BYTES


def _pdf_resource(server: MCPServer, presenter: FilePresenter) -> None:
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_invoice_pdf,
            reads=Parameters([Parameter("pk", "integer", required=True)]),
            presenter=presenter,
            permissions=OPEN,
        ),
        name="invoice-pdf",
        uri_template="invoices://{pk}/pdf",
    )


@_SYNC_AND_ASYNC
def test_a_resource_over_a_file_reads_as_a_blob_of_its_type(is_async: bool) -> None:
    server = _server()
    _pdf_resource(server, FilePresenter("application/pdf", filename=lambda row: "invoice.pdf"))

    (contents,) = _read(server, is_async, "invoices://7/pdf")["contents"]

    assert contents == {
        "uri": "invoices://7/pdf",
        "mimeType": "application/pdf",
        "blob": _b64(PDF_BYTES),
    }


@_SYNC_AND_ASYNC
def test_a_files_own_type_is_the_one_its_blob_carries(is_async: bool) -> None:
    render = lambda row: FileResult(row, filename="scan.pdf", content_type="application/x-pdf")  # noqa: E731
    server = _server()
    _pdf_resource(server, FilePresenter("application/pdf", render=render))

    (contents,) = _read(server, is_async, "invoices://7/pdf")["contents"]

    assert contents["mimeType"] == "application/x-pdf"


# ----- a tool over a ServiceSpec answers its file as a block -----


@_SYNC_AND_ASYNC
@pytest.mark.parametrize("era", [LEGACY, MODERN], ids=["legacy", "modern"])
def test_a_pdf_tool_answers_an_embedded_resource_carrying_the_blob(
    is_async: bool, era: str
) -> None:
    server = _server()
    _tool(
        server,
        "invoices.report",
        lambda: FileResult(io.BytesIO(PDF_BYTES), filename="report q3.pdf"),
        FilePresenter("application/pdf"),
    )

    result = _call(server, is_async, "invoices.report", era)

    assert "isError" not in result
    assert "structuredContent" not in result
    assert result["content"] == [
        {
            "type": "resource",
            "resource": {
                "uri": "spec-mcp://files/report%20q3.pdf",
                "mimeType": "application/pdf",
                "blob": _b64(PDF_BYTES),
            },
        }
    ]


@_SYNC_AND_ASYNC
def test_an_image_tool_answers_an_image_block(is_async: bool) -> None:
    server = _server()
    _tool(server, "chart", lambda: PNG_BYTES, FilePresenter("image/png", filename="c.png"))

    result = _call(server, is_async, "chart")

    assert result["content"] == [
        {"type": "image", "data": _b64(PNG_BYTES), "mimeType": "image/png"}
    ]


@_SYNC_AND_ASYNC
def test_a_tool_files_own_type_is_the_one_its_block_carries(is_async: bool) -> None:
    render = lambda raw: FileResult(raw, filename="c.jpg", content_type="image/jpeg")  # noqa: E731
    server = _server()
    _tool(server, "chart", lambda: PNG_BYTES, FilePresenter("image/png", render=render))

    (block,) = _call(server, is_async, "chart")["content"]

    assert block["mimeType"] == "image/jpeg"


@_SYNC_AND_ASYNC
def test_a_file_tool_presenting_nothing_answers_the_empty_object(is_async: bool) -> None:
    server = _server()
    _tool(server, "maybe", lambda: None, FilePresenter("image/png"), spec={"allow_none": True})

    result = _call(server, is_async, "maybe")

    assert result["content"] == [{"type": "text", "text": "{}"}]
    assert "structuredContent" not in result


@_SYNC_AND_ASYNC
def test_a_file_from_a_tool_presenting_no_file_is_an_encoding_error(is_async: bool) -> None:
    opened = _Opened()
    server = _server()
    server.register_tool(
        ServiceSpec(
            service=lambda: FileResult(opened(PDF_BYTES), filename="x.pdf"), permissions=OPEN
        ),
        name="bare",
        description="Returns a file and declares none.",
    )

    result = _call(server, is_async, "bare")

    assert result["isError"] is True
    assert "presents no file" in result["content"][0]["text"]
    assert opened.files[0].closed


# ----- a file past the ceiling answers the ceiling's error, and is closed -----


def _sized(opened: _Opened, *, declared: bool, size: int = CEILING * 4) -> FileResult:
    data = b"x" * size
    return FileResult(opened(data), filename="big.pdf", size=len(data) if declared else None)


@_SYNC_AND_ASYNC
@pytest.mark.parametrize("declared", [False, True], ids=["read-past", "declared-size"])
def test_a_tool_file_past_the_ceiling_is_result_too_large(is_async: bool, declared: bool) -> None:
    opened = _Opened()
    server = _server(config=build_mcp_config(max_result_bytes=CEILING))
    _tool(
        server, "big", lambda: _sized(opened, declared=declared), FilePresenter("application/pdf")
    )

    result = _call(server, is_async, "big")

    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["error"]["type"] == "result_too_large"
    assert "base64 is over this server's 1024 byte ceiling" in result["content"][0]["text"]
    assert opened.files[0].closed


@_SYNC_AND_ASYNC
def test_a_tools_own_ceiling_bounds_its_file(is_async: bool) -> None:
    opened = _Opened()
    server = _server()
    _tool(
        server,
        "big",
        lambda: _sized(opened, declared=False),
        FilePresenter("application/pdf"),
        max_result_bytes=CEILING,
    )

    result = _call(server, is_async, "big")

    assert json.loads(result["content"][0]["text"])["error"]["type"] == "result_too_large"
    assert "produced a file whose base64 is over" in result["content"][0]["text"]
    assert opened.files[0].closed


def _resource_answer(server: MCPServer, is_async: bool, uri: str) -> dict[str, Any]:
    meta = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    headers = {"Mcp-Protocol-Version": MODERN, "Mcp-Method": "resources/read", "Mcp-Name": uri}
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        client: Any = AsyncClient() if is_async else Client()
        response = _send(client, is_async, "resources/read", {"uri": uri, "_meta": meta}, headers)
    return json.loads(response.content)


@_SYNC_AND_ASYNC
@pytest.mark.parametrize("declared", [False, True], ids=["read-past", "declared-size"])
def test_a_resource_file_past_the_ceiling_is_the_servers_ceiling_error(
    is_async: bool, declared: bool
) -> None:
    opened = _Opened()
    server = _server(config=build_mcp_config(max_result_bytes=CEILING))
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda: _sized(opened, declared=declared),
            presenter=FilePresenter("application/pdf"),
            permissions=OPEN,
        ),
        name="big",
        uri_template="big://",
    )

    error = _resource_answer(server, is_async, "big://")["error"]

    assert error["code"] == -32000
    assert "Resource 'big://' produced a file" in error["message"]
    assert opened.files[0].closed


def test_a_file_handed_to_the_result_builder_by_hand_takes_the_bindings_type() -> None:
    file = FileResult(PNG_BYTES, filename="c.png")

    result = build_tool_result(
        file, content_kind=ToolContentKind.AUDIO, content_mime_type="audio/wav"
    ).to_dict()

    assert result["content"] == [
        {"type": "audio", "data": _b64(PNG_BYTES), "mimeType": "audio/wav"}
    ]


@pytest.mark.parametrize("raw", [PNG_BYTES, bytearray(PNG_BYTES), memoryview(PNG_BYTES)])
def test_media_bytes_handed_to_the_result_builder_by_hand_are_an_image_block(raw: Any) -> None:
    result = build_tool_result(
        raw, content_kind=ToolContentKind.IMAGE, content_mime_type="image/png"
    ).to_dict()

    assert result["content"] == [
        {"type": "image", "data": _b64(PNG_BYTES), "mimeType": "image/png"}
    ]


# ----- a file under the ceiling whose base64 is over it answers the file's wording -----


@_SYNC_AND_ASYNC
def test_a_tool_file_whose_base64_is_over_the_ceiling_is_refused_as_a_file(is_async: bool) -> None:
    opened = _Opened()
    server = _server(config=build_mcp_config(max_result_bytes=CEILING))
    big = lambda: _sized(opened, declared=False, size=900)  # noqa: E731
    _tool(server, "big", big, FilePresenter("application/pdf"))

    result = _call(server, is_async, "big")

    assert "produced a file whose base64 is over" in result["content"][0]["text"]
    assert opened.files[0].closed


@_SYNC_AND_ASYNC
def test_a_resource_file_whose_base64_is_over_the_ceiling_is_refused_as_a_file(
    is_async: bool,
) -> None:
    opened = _Opened()
    server = _server(config=build_mcp_config(max_result_bytes=CEILING))
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda: _sized(opened, declared=False, size=900),
            presenter=FilePresenter("application/pdf"),
            permissions=OPEN,
        ),
        name="big",
        uri_template="big://",
    )

    error = _resource_answer(server, is_async, "big://")["error"]

    assert error["code"] == -32000
    assert "produced a file whose base64 is over" in error["message"]
    assert opened.files[0].closed


# ----- a file the binding cannot carry is refused, and closed -----


@_SYNC_AND_ASYNC
def test_a_tool_file_of_another_kind_than_its_presenters_is_refused(is_async: bool) -> None:
    opened = _Opened()
    render = lambda raw: FileResult(opened(raw), filename="x.pdf", content_type="application/pdf")  # noqa: E731
    server = _server()
    _tool(server, "chart", lambda: PNG_BYTES, FilePresenter("image/png", render=render))

    result = _call(server, is_async, "chart")

    assert result["isError"] is True
    assert "'application/pdf' file, which a tool serves as RESOURCE" in result["content"][0]["text"]
    assert opened.files[0].closed


@_SYNC_AND_ASYNC
def test_a_file_from_a_resource_presenting_no_file_is_an_internal_error(is_async: bool) -> None:
    opened = _Opened()
    server = _server()
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda: FileResult(opened(PDF_BYTES), filename="a.pdf"),
            permissions=OPEN,
        ),
        name="bare",
        uri_template="bare://",
    )

    error = _resource_answer(server, is_async, "bare://")["error"]

    assert error["code"] == -32603
    assert (
        "Resource 'bare' produced a FileResult, and its spec presents no file" in error["message"]
    )
    assert opened.files[0].closed


def _text_chunks() -> FileResult:
    return FileResult(iter(["not bytes"]), filename="a.pdf")


@_SYNC_AND_ASYNC
def test_a_tool_file_yielding_text_is_an_encoding_error(is_async: bool) -> None:
    server = _server()
    _tool(server, "text", _text_chunks, FilePresenter("application/pdf"))

    result = _call(server, is_async, "text")

    assert json.loads(result["content"][0]["text"])["error"]["type"] == "output_encoding"
    assert "Tool 'text': FileResult.content gave a str" in result["content"][0]["text"]


@_SYNC_AND_ASYNC
def test_a_resource_file_yielding_text_is_an_internal_error(is_async: bool) -> None:
    server = _server()
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_text_chunks,
            presenter=FilePresenter("application/pdf"),
            permissions=OPEN,
        ),
        name="text",
        uri_template="text://",
    )

    error = _resource_answer(server, is_async, "text://")["error"]

    assert error["code"] == -32603
    assert "Resource 'text://': FileResult.content gave a str" in error["message"]


# ----- on the async route a file is read off the event loop -----


class _LoopProbe:
    """A file object recording whether each read ran on a running event loop."""

    def __init__(self) -> None:
        self.on_loop: list[bool] = []
        self._data = io.BytesIO(PDF_BYTES)

    def read(self, size: int = -1) -> bytes:
        try:
            asyncio.get_running_loop()
            self.on_loop.append(True)
        except RuntimeError:
            self.on_loop.append(False)
        return self._data.read(size)


def test_an_async_tool_reads_its_file_off_the_event_loop() -> None:
    probe = _LoopProbe()
    server = _server()
    _tool(
        server, "doc", lambda: FileResult(probe, filename="a.pdf"), FilePresenter("application/pdf")
    )

    _call(server, True, "doc")

    assert probe.on_loop and not any(probe.on_loop)


def test_an_async_resource_reads_its_file_off_the_event_loop() -> None:
    probe = _LoopProbe()
    server = _server()
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda: FileResult(probe, filename="a.pdf"),
            presenter=FilePresenter("application/pdf"),
            permissions=OPEN,
        ),
        name="doc",
        uri_template="doc://",
    )

    _resource_answer(server, True, "doc://")

    assert probe.on_loop and not any(probe.on_loop)
