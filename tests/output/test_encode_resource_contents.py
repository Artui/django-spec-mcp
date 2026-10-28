"""How a kernel resource's presented value becomes its ``resources/read`` body.

``encode_resource_contents`` is the one encode step both reads share: the
drf-services read calls it after its serializer step, and a kernel read after
the spec's presenter. Its questions are asked here of ``resources/read`` on a
registered kernel resource, which is the answer a client gets, so they hold
whichever caller survives:

- ``TEXT`` serves the presented ``str`` verbatim, under the mime type
  registered, including the default ``application/json``, on a binding built
  by hand as on a registered one;
- ``BLOB`` base64-encodes presented bytes into ``blob`` and sets no ``text``;
- a value of the wrong type is a well-formed ``INTERNAL_ERROR`` naming the
  presenter, never the drf-services path's ``output_serializer``;
- the result ceiling and the cache hints apply as they do to ``JSON``;
- a presenter refusing a shape the URI carried, and a missing row, are answered
  as on a ``JSON`` resource.

The drf-services read's own questions of the same step stay in
``test_build_resource_contents_drfs.py``.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django_service_specs import (
    Output,
    Parameter,
    Parameters,
    Presenter,
    SelectorKind,
    SelectorSpec,
    ServiceValidationError,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.utils import resource_not_found_code
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.resource_encoding import ResourceEncoding

PNG: bytes = b"\x89PNG\r\n\x1a\n\x00\x01"


@dataclass
class _Note:
    pk: int
    title: str


def _note(*, pk: int) -> _Note | None:
    return None if pk == 404 else _Note(pk=pk, title=f"Note {pk}")


class _Document(Presenter):
    """A presenter answering one document rather than an object of fields.

    It declares no output fields: what it renders is a body, not a row.
    """

    def output(self) -> Output:
        return Output()


class _MarkdownPresenter(_Document):
    """Renders a row as a Markdown document, as a consumer's presenter would."""

    def present(self, value: Any) -> Any:
        return f"# {value.title}\n\nNote number {value.pk}.\n"


class _Answers(_Document):
    """Answers ``value`` whatever the row, to hand the encoder a given type."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def present(self, value: Any) -> Any:
        return self.value


class _RefusesShape(_Document):
    """Refuses every value of the ``style`` shape, as a strict renderer does."""

    def present(self, value: Any) -> Any:
        raise ServiceValidationError("No such style.")


def _server(
    presenter: Presenter | None,
    *,
    uri_template: str = "notes://{pk}",
    selector: Any = _note,
    shapes: tuple[str, ...] = (),
    **register: Any,
) -> MCPServer:
    reads = [Parameter("pk", "integer", required=True)] if "{pk}" in uri_template else []
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=selector,
            reads=Parameters(reads),
            shapes=Parameters([Parameter(name, "string") for name in shapes]),
            presenter=presenter,
            permissions=[Unrestricted()],
        ),
        name="note",
        uri_template=uri_template,
        **register,
    )
    return server


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def is_async(request: pytest.FixtureRequest) -> bool:
    return request.param


def _read(server: MCPServer, uri: str, *, is_async: bool, **config: Any) -> Any:
    context = server._call_context(user=None)
    if config:
        context = replace(context, config=build_mcp_config(**config))
    params = {"uri": uri}
    if is_async:
        return async_to_sync(handle_resources_read_async)(params, context)
    return handle_resources_read(params, context)


def _block(server: MCPServer, uri: str, *, is_async: bool) -> dict[str, Any]:
    answer = _read(server, uri, is_async=is_async)
    assert not isinstance(answer, JsonRpcError), answer
    [block] = answer["contents"]
    return block


class TestText:
    def test_a_presenter_rendering_markdown_is_served_verbatim(self, is_async: bool) -> None:
        server = _server(
            _MarkdownPresenter(), mime_type="text/markdown", encoding=ResourceEncoding.TEXT
        )
        block = _block(server, "notes://7", is_async=is_async)
        assert block == {
            "uri": "notes://7",
            "mimeType": "text/markdown",
            "text": "# Note 7\n\nNote number 7.\n",
        }

    def test_with_no_presenter_the_selectors_str_is_the_body(self, is_async: bool) -> None:
        # The kernel hands a presenter-less RETRIEVE's value through unchanged.
        server = _server(
            None,
            uri_template="notes://readme",
            selector=lambda: "plain, as written",
            mime_type="text/plain",
            encoding=ResourceEncoding.TEXT,
        )
        assert _block(server, "notes://readme", is_async=is_async)["text"] == "plain, as written"

    def test_under_the_default_json_mime_the_str_is_still_verbatim(self, is_async: bool) -> None:
        # A presenter that encodes its own JSON: TEXT never re-encodes it, and the
        # mime type is the one registered, never inferred from the encoding.
        server = _server(_Answers('{"compact":true}'), encoding=ResourceEncoding.TEXT)
        block = _block(server, "notes://1", is_async=is_async)
        assert (block["mimeType"], block["text"]) == ("application/json", '{"compact":true}')

    def test_a_handbuilt_binding_is_encoded_as_a_registered_one(self, is_async: bool) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda: "built by hand",
            permissions=[Unrestricted()],
        )
        server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
        server.resources.register(
            KernelResourceBinding(
                name="hand",
                uri_template="hand://",
                description=None,
                selector=spec.selector,
                kind=spec.kind,
                spec=spec,
                mime_type="text/plain",
                encoding=ResourceEncoding.TEXT,
            )
        )
        block = _block(server, "hand://", is_async=is_async)
        assert (block["mimeType"], block["text"]) == ("text/plain", "built by hand")


class TestBlob:
    # Each member of the isinstance tuple is held by its own case: the tuple is
    # one branch arc, so coverage would not notice one deleted.
    @pytest.mark.parametrize("kind", [bytes, bytearray, memoryview])
    def test_bytes_like_are_base64_in_blob_with_no_text(self, kind: type, is_async: bool) -> None:
        server = _server(_Answers(kind(PNG)), mime_type="image/png", encoding=ResourceEncoding.BLOB)
        block = _block(server, "notes://1", is_async=is_async)
        assert block == {
            "uri": "notes://1",
            "mimeType": "image/png",
            "blob": base64.b64encode(PNG).decode("ascii"),
        }

    def test_a_non_contiguous_memoryview_is_encoded_as_its_bytes(self, is_async: bool) -> None:
        # A strided view is bytes-like but not a buffer base64 can read in place.
        server = _server(
            _Answers(memoryview(b"abcdef")[::2]),
            mime_type="application/octet-stream",
            encoding=ResourceEncoding.BLOB,
        )
        block = _block(server, "notes://1", is_async=is_async)
        assert base64.b64decode(block["blob"]) == b"ace"


class TestWrongType:
    @pytest.mark.parametrize(
        ("encoding", "value", "says"),
        [
            (
                ResourceEncoding.TEXT,
                {"not": "a document"},
                "encoding=TEXT but produced dict, not str",
            ),
            (ResourceEncoding.BLOB, "not bytes", "encoding=BLOB but produced str, not bytes"),
        ],
        ids=["text", "blob"],
    )
    def test_is_an_internal_error_naming_the_presenter(
        self, encoding: ResourceEncoding, value: Any, says: str, is_async: bool
    ) -> None:
        error = _read(_server(_Answers(value), encoding=encoding), "notes://1", is_async=is_async)
        assert isinstance(error, JsonRpcError)
        assert error.code == JsonRpcErrorCode.INTERNAL_ERROR
        assert error.message.startswith(f"Resource 'note' declares {says}.")
        assert "the spec's presenter" in error.message
        # The drf-services wording would send its reader to a knob this form lacks.
        assert "output_serializer" not in error.message


class TestEnvelope:
    @pytest.mark.parametrize(
        ("encoding", "value"),
        [(ResourceEncoding.TEXT, "x" * 500), (ResourceEncoding.BLOB, b"x" * 500)],
        ids=["text", "blob"],
    )
    def test_a_body_over_the_ceiling_is_refused_as_json_is(
        self, encoding: ResourceEncoding, value: Any, is_async: bool
    ) -> None:
        error = _read(
            _server(_Answers(value), encoding=encoding),
            "notes://1",
            is_async=is_async,
            max_result_bytes=200,
        )
        assert isinstance(error, JsonRpcError)
        assert error.code == JsonRpcErrorCode.SERVER_ERROR
        assert "byte ceiling" in error.message
        assert "Resource 'notes://1'" in error.message

    # Each encoding builds its own block, so each is held to carrying the meta.
    @pytest.mark.parametrize(
        ("presenter", "encoding"),
        [
            (_MarkdownPresenter(), ResourceEncoding.TEXT),
            (_Answers(PNG), ResourceEncoding.BLOB),
        ],
        ids=["text", "blob"],
    )
    def test_the_cache_hints_and_meta_ride_beside_the_body(
        self, presenter: Presenter, encoding: ResourceEncoding, is_async: bool
    ) -> None:
        server = _server(presenter, encoding=encoding, cache_ttl_ms=60_000, meta={"x": 1})
        answer = _read(server, "notes://2", is_async=is_async)
        assert (answer["ttlMs"], answer["contents"][0]["_meta"]) == (60_000, {"x": 1})


class TestRefusals:
    def test_a_presenter_refusing_a_uri_shape_is_invalid_params(self, is_async: bool) -> None:
        server = _server(
            _RefusesShape(),
            uri_template="notes://{pk}/{style}",
            shapes=("style",),
            mime_type="text/markdown",
            encoding=ResourceEncoding.TEXT,
        )
        error = _read(server, "notes://1/fancy", is_async=is_async)
        assert isinstance(error, JsonRpcError)
        assert (error.code, error.message) == (JsonRpcErrorCode.INVALID_PARAMS, "Invalid arguments")
        assert error.data == {"detail": {"style": ["No such style."]}}

    def test_a_missing_row_is_resource_not_found(self, is_async: bool) -> None:
        server = _server(
            _MarkdownPresenter(), mime_type="text/markdown", encoding=ResourceEncoding.TEXT
        )
        error = _read(server, "notes://404", is_async=is_async)
        assert isinstance(error, JsonRpcError)
        protocol_version = server._call_context(user=None).protocol_version
        assert error.code == resource_not_found_code(protocol_version)
        assert (error.message, error.data) == (
            "Resource not found: 'notes://404'",
            {"uri": "notes://404"},
        )
