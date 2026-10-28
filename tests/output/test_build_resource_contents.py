"""What ``resources/read`` serves in its one ``contents`` block.

``build_resource_contents`` encodes the block for every resource that is not
the kernel's: a view, which reads with nothing of drf-services, and a resource
lifted from a drf-services ``SelectorSpec``, whose output serializer renders
first (``render_drfs_resource_output``, asked in
``test_render_drfs_resource_output_drfs.py``). So the encoding is asked of the
function directly here: ``JSON`` pretty-prints, ``TEXT`` is verbatim, ``BLOB``
is base64, a body of the wrong type is a clean error, and the block carries the
URI, the MIME type and a copy of the binding's ``_meta``.

A kernel read builds its block in ``kernel_utils.resource_read_result``, so the
same questions are asked of ``resources/read`` on a registered kernel resource,
which is the answer a client gets:

- the presented row is pretty-printed JSON, by default;
- the block echoes the URI asked for, with the binding's ``mimeType``;
- the binding's ``_meta`` is carried, and omitted when empty.

The original's questions of ``encoding=TEXT`` and ``encoding=BLOB`` and of a
``mime_type`` other than JSON are asked of a kernel resource in
``test_encode_resource_contents.py``, since both reads encode through
``encode_resource_contents``. Two have no kernel counterpart: an
``output_serializer`` rendering a ``RETRIEVE`` or a ``LIST``, because the
registrar refuses that knob (the spec's presenter renders the row, and a kernel
resource is a ``RETRIEVE``), and whether the block's ``_meta`` is a copy of the
binding's, which the original asks of the returned dataclass, where a kernel
read hands a client only the encoded body, which holds no reference to share.
The original's rendering cases are asked in
``test_render_drfs_resource_output_drfs.py``.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import pytest
from django.test import Client, override_settings
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.output.build_resource_contents import build_resource_contents
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.resource_contents import ResourceContents
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.resource_encoding import ResourceEncoding
from tests.kernel_client import KernelClient
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.urlconf_for import urlconf_for


@dataclass
class _Thing:
    label: str
    qty: int = 0


def _thing(*, pk: int) -> _Thing:
    return _Thing(label=f"thing {pk}", qty=pk)


def _server(**register: Any) -> MCPServer:
    server = MCPServer(
        name="contents", auth_backend=HeaderAuthBackend(), session_store=InMemorySessionStore()
    )
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_thing,
            reads=Parameters([Parameter("pk", "integer", required=True)]),
            presenter=DataclassPresenter(_Thing),
            permissions=[Unrestricted()],
        ),
        name="thing",
        uri_template="things://{pk}",
        **register,
    )
    return server


def _contents(client: Client, server: MCPServer, *, is_async: bool) -> dict[str, Any]:
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        caller = KernelClient(client)
        caller.initialize()
        body = caller.post("resources/read", {"uri": "things://2"}).json()
    [block] = body["result"]["contents"]
    return block


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def is_async(request: pytest.FixtureRequest) -> bool:
    return request.param


class TestEncoding:
    def test_the_presented_row_is_pretty_printed_json(self, client: Client, is_async: bool) -> None:
        block = _contents(client, _server(), is_async=is_async)
        assert block["text"] == '{\n  "label": "thing 2",\n  "qty": 2\n}'
        assert "blob" not in block


class TestEnvelope:
    def test_carries_the_uri_asked_for_and_the_json_mime_type(
        self, client: Client, is_async: bool
    ) -> None:
        block = _contents(client, _server(meta={"x": 1}), is_async=is_async)
        assert (block["uri"], block["mimeType"], block["_meta"]) == (
            "things://2",
            "application/json",
            {"x": 1},
        )

    def test_an_empty_meta_is_omitted(self, client: Client, is_async: bool) -> None:
        assert "_meta" not in _contents(client, _server(), is_async=is_async)


# ---------- the encoding, asked of the function ----------


def _binding(**overrides: Any) -> ResourceBinding:
    defaults: dict[str, Any] = {
        "name": "thing",
        "uri_template": "things://x",
        "description": None,
        "selector": lambda: None,
        "kind": SelectorKind.RETRIEVE,
    }
    return ResourceBinding(**{**defaults, **overrides})


def _encoded(binding: ResourceBinding, raw: Any, uri: str = "things://x") -> ResourceContents:
    contents = build_resource_contents(binding=binding, uri=uri, raw=raw)
    assert not isinstance(contents, JsonRpcError), contents
    return contents


class TestEncodingTheBody:
    def test_json_is_the_default(self) -> None:
        assert _encoded(_binding(), {"a": 1}).text == '{\n  "a": 1\n}'

    def test_text_returns_the_document_verbatim(self) -> None:
        """The whole point: JSON-encoding HTML yields a quoted string literal."""
        binding = _binding(encoding=ResourceEncoding.TEXT)
        assert _encoded(binding, "<h1>Hi</h1>").text == "<h1>Hi</h1>"

    def test_json_encoding_would_have_quoted_it(self) -> None:
        """Pins the behaviour TEXT exists to avoid, so a regression is visible."""
        assert _encoded(_binding(), "<h1>Hi</h1>").text == '"<h1>Hi</h1>"'

    def test_blob_is_base64_in_the_blob_field_alone(self) -> None:
        contents = _encoded(_binding(encoding=ResourceEncoding.BLOB), b"\x00\x01png")
        assert contents.blob == base64.b64encode(b"\x00\x01png").decode("ascii")
        assert contents.text is None

    @pytest.mark.parametrize(
        ("encoding", "raw", "named"),
        [
            (ResourceEncoding.TEXT, {"not": "html"}, "dict"),
            (ResourceEncoding.BLOB, "not bytes", "str"),
        ],
    )
    def test_a_body_of_the_wrong_type_is_a_json_rpc_error(
        self, encoding: ResourceEncoding, raw: Any, named: str
    ) -> None:
        """A misconfiguration, but it must not become a transport-level 500 —
        the selector's return type isn't knowable at registration."""
        result = build_resource_contents(binding=_binding(encoding=encoding), uri="u://", raw=raw)

        assert isinstance(result, JsonRpcError)
        assert result.code == JsonRpcErrorCode.INTERNAL_ERROR
        assert f"encoding={encoding.name}" in result.message
        assert f"produced {named}," in result.message


class TestTheBlock:
    def test_carries_the_bindings_uri_mime_type_and_meta(self) -> None:
        contents = _encoded(_binding(mime_type="text/csv", meta={"x": 1}), [], "things://actual")
        assert (contents.uri, contents.mime_type, contents.meta) == (
            "things://actual",
            "text/csv",
            {"x": 1},
        )

    def test_an_empty_meta_is_omitted(self) -> None:
        assert _encoded(_binding(), []).meta is None

    def test_the_meta_is_copied_not_shared_with_the_binding(self) -> None:
        binding = _binding(meta={"x": 1})
        assert _encoded(binding, []).meta is not binding.meta
