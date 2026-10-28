"""What a kernel resource's ``resources/read`` serves in its one ``contents`` block.

The original asks ``build_resource_contents`` directly, and that function is the
drf-services read's alone: both read handlers call it only past the
``runs_on_kernel`` split, and a kernel read builds its block in
``kernel_utils.resource_read_result``. So the questions are asked here of
``resources/read`` on a registered kernel resource, which is the answer a client
gets:

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
The original is kept beside this file as
``test_build_resource_contents_drfs.py``.
"""

from __future__ import annotations

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
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
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
