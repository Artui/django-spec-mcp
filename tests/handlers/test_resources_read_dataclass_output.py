"""``resources/read`` renders a kernel resource through its spec's ``DataclassPresenter``.

The original asked that a resource whose drf-services selector declares a raw
dataclass as its ``output_serializer`` renders it, since the read resolved the
output itself rather than through ``render_spec_output``. A kernel resource has
no such step: ``register_resource(spec, uri_template=..., name=...)`` reads
through the kernel's ``dispatch`` and the spec's presenter renders the row, so
what is asked here is that both handlers serve what the presenter presents.

The original's other questions, in their kernel form:

- a ``LIST`` of dataclasses: a kernel resource reads one row, so a ``LIST`` is
  refused at registration and served as a selector tool instead;
- an output no read could render: a presenter that is not a ``Presenter`` is
  refused when the spec is built, and ``output_serializer=`` beside a kernel
  spec is refused at registration, since the spec declares its own presenter;
- a declaration every read would fail on, refused at registration rather than
  at the first read: a template variable no read declares. That one is not
  refused yet, so its test is a strict xfail naming the fix.

The original is kept beside this file as ``test_resources_read_dataclass_output_drfs.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.types.context import MCPCallContext

OPEN = [Unrestricted()]
_PK = Parameters([Parameter("pk", "integer", required=True)])


@dataclass
class Invoice:
    number: str
    amount_cents: int


def _invoice(*, pk: int) -> Invoice:
    return Invoice(number=f"INV-{pk}", amount_cents=100)


def _server() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_invoice,
            reads=_PK,
            presenter=DataclassPresenter(Invoice),
            permissions=OPEN,
        ),
        name="invoice",
        uri_template="invoices://{pk}",
    )
    return server


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def _body(out: Any) -> Any:
    assert isinstance(out, dict), out
    return json.loads(out["contents"][0]["text"])


@pytest.mark.django_db
def test_sync_read_renders_a_dataclass_retrieve() -> None:
    out = handle_resources_read({"uri": "invoices://7"}, _ctx(_server()))
    assert _body(out) == {"number": "INV-7", "amount_cents": 100}


@pytest.mark.django_db(transaction=True)
async def test_async_read_renders_a_dataclass_retrieve() -> None:
    out = await handle_resources_read_async({"uri": "invoices://7"}, _ctx(_server()))
    assert _body(out) == {"number": "INV-7", "amount_cents": 100}


def test_a_list_of_dataclasses_is_a_tool_not_a_resource() -> None:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    with pytest.raises(ImproperlyConfigured, match="RETRIEVE, not a LIST"):
        server.register_resource(
            SelectorSpec(
                kind=SelectorKind.LIST,
                selector=lambda: [Invoice("INV-1", 100)],
                presenter=DataclassPresenter(Invoice),
                permissions=OPEN,
            ),
            name="invoices",
            uri_template="invoices://all",
        )


def test_a_presenter_no_read_could_render_through_is_refused_when_the_spec_is_built() -> None:
    with pytest.raises(ImproperlyConfigured, match="not a Presenter"):
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda: {},
            presenter=Invoice,  # ty: ignore[invalid-argument-type] - the refusal under test
            permissions=OPEN,
        )


def test_an_output_serializer_beside_a_kernel_spec_is_refused() -> None:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    with pytest.raises(TypeError, match="output_serializer"):
        server.register_resource(
            SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: {}, permissions=OPEN),
            name="bad",
            uri_template="bad://x",
            output_serializer=Invoice,
        )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "register_resource does not compare a kernel spec's reads with the template's "
        "variables, so every read of this resource is refused as invalid params; the "
        "fix is a check in MCPServer._register_kernel_resource."
    ),
)
def test_a_template_variable_no_read_declares_is_refused_at_registration() -> None:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    with pytest.raises(ImproperlyConfigured, match="pk"):
        server.register_resource(
            SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: {}, permissions=OPEN),
            name="bad",
            uri_template="bad://{pk}",
        )
