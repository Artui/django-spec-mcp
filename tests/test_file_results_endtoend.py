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
    UnsetType,
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


# ----- a file past the ceiling is described rather than sent, and closed -----


def _sized(opened: _Opened, *, declared: bool, size: int = CEILING * 4) -> FileResult:
    data = b"x" * size
    return FileResult(opened(data), filename="big.pdf", size=len(data) if declared else None)


def _note(size: str, ceiling: int = CEILING, mime: str = "application/pdf") -> str:
    return (
        f"[big.pdf] is a {mime} file ({size}), over the {ceiling}-byte limit for returning "
        "a file's contents once base64-encoded; it was described rather than sent."
    )


def _no_remedy(text: str) -> None:
    assert "'limit'" not in text
    assert "filter" not in text
    assert "Narrow" not in text


def _described_tool(result: dict[str, Any]) -> str:
    assert result.get("isError") is not True
    (block,) = result["content"]
    assert block["type"] == "text"
    _no_remedy(block["text"])
    return block["text"]


@_SYNC_AND_ASYNC
@pytest.mark.parametrize("declared", [False, True], ids=["read-past", "declared-size"])
def test_a_tool_file_past_the_ceiling_is_described(is_async: bool, declared: bool) -> None:
    opened = _Opened()
    server = _server(config=build_mcp_config(max_result_bytes=CEILING))
    _tool(
        server, "big", lambda: _sized(opened, declared=declared), FilePresenter("application/pdf")
    )

    text = _described_tool(_call(server, is_async, "big"))

    if declared:
        assert text == _note("4096 bytes")
    else:
        assert text.startswith("[big.pdf] is a application/pdf file (at least ")
        assert text.endswith("it was described rather than sent.")
    assert opened.files[0].closed


@_SYNC_AND_ASYNC
def test_a_tools_own_ceiling_bounds_its_file(is_async: bool) -> None:
    opened = _Opened()
    server = _server()
    _tool(
        server,
        "big",
        lambda: _sized(opened, declared=True),
        FilePresenter("application/pdf"),
        max_result_bytes=CEILING,
    )

    assert _described_tool(_call(server, is_async, "big")) == _note("4096 bytes")
    assert opened.files[0].closed


def test_an_in_process_call_describes_a_file_past_the_ceiling() -> None:
    server = _server(config=build_mcp_config(max_result_bytes=CEILING))
    _tool(server, "big", lambda: _sized(_Opened(), declared=True), FilePresenter("application/pdf"))

    result = server.call_tool("big", {}, user=None).to_dict()

    assert _described_tool(result) == _note("4096 bytes")


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


def _big_resource(server: MCPServer, selector: Any, **knobs: Any) -> Any:
    return server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=selector,
            presenter=FilePresenter("application/pdf"),
            permissions=OPEN,
        ),
        name="big",
        uri_template="big://",
        **knobs,
    )


def _described_resource(answer: dict[str, Any]) -> str:
    (contents,) = answer["result"]["contents"]
    assert contents["uri"] == "big://"
    assert contents["mimeType"] == "text/plain"
    assert "blob" not in contents
    _no_remedy(contents["text"])
    return contents["text"]


@_SYNC_AND_ASYNC
@pytest.mark.parametrize("declared", [False, True], ids=["read-past", "declared-size"])
def test_a_resource_file_past_the_ceiling_is_described(is_async: bool, declared: bool) -> None:
    opened = _Opened()
    server = _server(config=build_mcp_config(max_result_bytes=CEILING))
    _big_resource(server, lambda: _sized(opened, declared=declared))

    text = _described_resource(_resource_answer(server, is_async, "big://"))

    if declared:
        assert text == _note("4096 bytes")
    else:
        assert text.startswith("[big.pdf] is a application/pdf file (at least ")
    assert opened.files[0].closed


@_SYNC_AND_ASYNC
def test_a_resources_own_ceiling_bounds_its_file(is_async: bool) -> None:
    opened = _Opened()
    server = _server()
    _big_resource(server, lambda: _sized(opened, declared=True), max_result_bytes=CEILING)

    text = _described_resource(_resource_answer(server, is_async, "big://"))

    assert text == _note("4096 bytes")
    assert opened.files[0].closed


@_SYNC_AND_ASYNC
def test_a_resources_own_ceiling_raises_the_servers_for_its_file(is_async: bool) -> None:
    server = _server(config=build_mcp_config(max_result_bytes=CEILING))
    _big_resource(
        server, lambda: _sized(_Opened(), declared=True, size=2000), max_result_bytes=8192
    )

    (contents,) = _resource_answer(server, is_async, "big://")["result"]["contents"]

    assert contents["blob"] == _b64(b"x" * 2000)


@_SYNC_AND_ASYNC
def test_a_tool_whose_own_ceiling_is_none_sends_its_file_whole(is_async: bool) -> None:
    server = _server(config=build_mcp_config(max_result_bytes=CEILING))
    file = lambda: _sized(_Opened(), declared=False)  # noqa: E731
    _tool(server, "big", file, FilePresenter("application/pdf"), max_result_bytes=None)

    (block,) = _call(server, is_async, "big")["content"]

    assert block["resource"]["blob"] == _b64(b"x" * CEILING * 4)


@_SYNC_AND_ASYNC
def test_a_resource_whose_own_ceiling_is_none_sends_its_file_whole(is_async: bool) -> None:
    server = _server(config=build_mcp_config(max_result_bytes=CEILING))
    _big_resource(server, lambda: _sized(_Opened(), declared=False), max_result_bytes=None)

    (contents,) = _resource_answer(server, is_async, "big://")["result"]["contents"]

    assert contents["blob"] == _b64(b"x" * CEILING * 4)


def test_a_resource_registered_without_a_ceiling_defers_to_the_servers() -> None:
    binding = _big_resource(_server(), lambda: b"")

    assert isinstance(binding.max_result_bytes, UnsetType)


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


# ----- a file whose answer is over the ceiling, envelope included, is described too -----


@_SYNC_AND_ASYNC
def test_a_tool_file_in_the_envelope_band_is_described_not_refused(is_async: bool) -> None:
    opened = _Opened()
    server = _server(config=build_mcp_config(max_result_bytes=CEILING))
    _tool(
        server,
        "big",
        lambda: _sized(opened, declared=False, size=760),
        FilePresenter("application/pdf"),
    )

    text = _described_tool(_call(server, is_async, "big"))

    assert text.startswith("[big.pdf] is a application/pdf file (at least ")
    assert opened.files[0].closed


@_SYNC_AND_ASYNC
def test_a_resource_file_in_the_envelope_band_is_described_not_refused(is_async: bool) -> None:
    opened = _Opened()
    server = _server(config=build_mcp_config(max_result_bytes=CEILING))
    _big_resource(server, lambda: _sized(opened, declared=False, size=700))

    text = _described_resource(_resource_answer(server, is_async, "big://"))

    assert text.startswith("[big.pdf] is a application/pdf file (at least ")
    assert opened.files[0].closed


def _sweep(answer: Any) -> list[bool]:
    sent: list[bool] = []
    for size in range(560, 800):
        served, text = answer(size)
        if not served:
            assert text.startswith("[big.pdf] is a application/pdf file (at least "), size
        sent.append(served)
    return sent


def test_every_tool_file_size_is_either_sent_whole_or_described() -> None:
    server = _server(config=build_mcp_config(max_result_bytes=CEILING))
    held: list[int] = [0]
    _tool(
        server,
        "big",
        lambda: FileResult(io.BytesIO(b"x" * held[0]), filename="big.pdf"),
        FilePresenter("application/pdf"),
    )

    def answer(size: int) -> tuple[bool, str]:
        held[0] = size
        result = _call(server, False, "big")
        assert result.get("isError") is not True, size
        (block,) = result["content"]
        if block["type"] == "resource":
            assert block["resource"]["blob"] == _b64(b"x" * size)
            return True, ""
        return False, block["text"]

    sent = _sweep(answer)

    assert sent[0] and not sent[-1]
    assert sent == sorted(sent, reverse=True)


def test_every_resource_file_size_is_either_sent_whole_or_described() -> None:
    server = _server(config=build_mcp_config(max_result_bytes=CEILING))
    held: list[int] = [0]
    _big_resource(server, lambda: FileResult(io.BytesIO(b"x" * held[0]), filename="big.pdf"))

    def answer(size: int) -> tuple[bool, str]:
        held[0] = size
        (contents,) = _resource_answer(server, False, "big://")["result"]["contents"]
        if "blob" in contents:
            assert contents["blob"] == _b64(b"x" * size)
            return True, ""
        return False, contents["text"]

    sent = _sweep(answer)

    assert sent[0] and not sent[-1]
    assert sent == sorted(sent, reverse=True)


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
