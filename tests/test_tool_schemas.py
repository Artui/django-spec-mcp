"""The advertised tool schemas, pinned as the wire serves them.

A deployed agent holds what ``tools/list`` advertised: each tool's name, its
description, its ``inputSchema`` and the rest. A change to any of them is a
change to an API whose callers are not in this repository, so the listing is
checked in at ``tests/testapp/tool_schemas.json`` and this test diffs it.

The dump is what the wire answers: a ``tools/list`` posted through the HTTP
endpoint, every page of it, read back out of the JSON-RPC response. Nothing
here builds an entry by hand, so the file moves when the server's output
does, and only then.

The server below is built for the dump, over the test app's shapes: one tool
per schema feature the listing carries, not one per test spec. Adding a
feature to the listing earns a tool here.

A schema change regenerates the dump in the same pull request::

    make tool-schemas

which runs this test with ``TOOL_SCHEMAS_WRITE=1`` set, so it writes the file
instead of comparing and then skips, so a write is never reported as a pass; the
diff of ``tool_schemas.json`` is then the change a reviewer reads.
"""

from __future__ import annotations

import difflib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import django_filters
import pytest
from django.test import Client, override_settings
from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceSpec,
    UnknownArguments,
    Unrestricted,
    ValidationContext,
    Validator,
    call_transport,
)

from django_spec_mcp import (
    MCP_TRANSPORT,
    Icon,
    IconTheme,
    MCPServer,
    ToolContentKind,
    UIToolMeta,
    UIVisibility,
)
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.server.utils import UnboundedListWarning
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.conftest import post_jsonrpc
from tests.testapp.kernel_shapes import AgentInvoiceOutput, InvoiceInput, InvoiceOutput
from tests.testapp.models import Invoice
from tests.testapp.services import invoice_by_pk, list_invoices, record_invoice
from tests.testapp.urlconf_for import urlconf_for

DUMP = Path(__file__).parent / "testapp" / "tool_schemas.json"
WRITE = os.environ.get("TOOL_SCHEMAS_WRITE") == "1"

# The unpaged list is registered on purpose: its schema is the one without
# ``page`` and ``limit``.
pytestmark = pytest.mark.filterwarnings(
    f"ignore::{UnboundedListWarning.__module__}.UnboundedListWarning"
)


class _SortedInvoices(django_filters.FilterSet):
    """An ``ordering`` the kernel reads into the spec's reads, as an enum."""

    sent = django_filters.BooleanFilter()
    ordering = django_filters.OrderingFilter(fields=(("amount_cents", "amount"),))

    class Meta:
        model = Invoice
        fields = ["sent"]


def _invoices(**_: Any) -> Any:
    return Invoice.objects.all()


def _invoice_of(*, project: int, draw: int = 0, note: str = "") -> Invoice | None:
    return None


def _maybe_invoice(**_: Any) -> Invoice | None:
    """A selector that may find nothing, for ``allow_none``."""
    return None


def _maybe_invoices() -> list[Invoice] | None:
    """A service that returns rows or nothing, for ``returns_many`` with ``allow_none``."""
    return None


def _chart() -> bytes:
    """A service whose result is an image rather than JSON text."""
    return b""


class _PerTransport(Validator):
    """``amount`` to anyone, and a required field to each of two transports."""

    def parameters(self, *, call_context: dict[str, Any] | None = None) -> Parameters:
        declared = [Parameter("amount", "integer", required=True)]
        if call_context is not None and call_transport(call_context) == MCP_TRANSPORT:
            declared.append(Parameter("session_ref", "string", required=True))
        if call_context is not None and call_transport(call_context) == "cli":
            declared.append(Parameter("operator", "string", required=True))
        return Parameters(declared)

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        return dict(arguments)


def _server() -> MCPServer:
    server = MCPServer(
        name="tool-schemas", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    # A service: a required field, a defaulted one, and the registration's
    # display fields.
    server.register_tool(
        ServiceSpec(
            service=record_invoice,
            validator=DataclassValidator(InvoiceInput),
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
        ),
        name="invoices.create",
        description="Create an invoice.",
        title="Create invoice",
        icons=(
            Icon(
                src="https://example.test/i.png",
                mime_type="image/png",
                sizes=("48x48",),
                theme=IconTheme.DARK,
            ),
        ),
        annotations={"destructiveHint": False, "idempotentHint": False},
        meta={"example.test/owner": "billing"},
    )
    # A retrieve: a required read, and an output marked for the agent audience,
    # so the handle line joins the description and a hidden field leaves the
    # output schema.
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=invoice_by_pk,
            reads=Parameters([Parameter("pk", "integer", required=True)]),
            presenter=DataclassPresenter(AgentInvoiceOutput),
            permissions=[Unrestricted()],
        ),
        name="invoices.get",
        description="Fetch a single invoice by id.",
        annotations={"readOnlyHint": True},
    )
    # A paged list: ``page`` and ``limit`` under a bound, a filter set's
    # ``ordering`` and filter, and read-shaping ``fields`` (with help) and
    # ``expand`` (without).
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_invoices,
            filter_set=_SortedInvoices,
            shapes=Parameters(
                [
                    Parameter("fields", "string", help="Fields to return."),
                    Parameter("expand", "string"),
                ]
            ),
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
        ),
        name="invoices.search",
        description="Search invoices.",
        paginate=True,
        max_page_size=50,
    )
    # An unpaged list, the shape a list tool has without ``paginate``.
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_invoices,
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
        ),
        name="invoices.list",
        description="List every invoice.",
    )
    # Server arguments: ``project`` bound to a value, ``draw`` withheld, so
    # only the optional ``note`` is left for the caller.
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_invoice_of,
            reads=Parameters(
                [
                    Parameter("project", "integer", required=True),
                    Parameter("draw", "integer"),
                    Parameter("note", "string", help="A note to match."),
                ]
            ),
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
        ),
        name="invoices.in_project",
        description="Fetch the bound project's invoice.",
        server_arguments=ServerArguments(values={"project": 7}, withheld=frozenset({"draw"})),
    )
    # A retrieve that may find nothing: ``allow_none`` lets the output schema
    # admit an empty result.
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_maybe_invoice,
            allow_none=True,
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
        ),
        name="invoices.maybe",
        description="Fetch an invoice, if there is one.",
    )
    # A service returning many rows, or nothing: ``returns_many`` makes the
    # output an array, and ``allow_none`` keeps it an array that may be empty.
    server.register_tool(
        ServiceSpec(
            service=_maybe_invoices,
            returns_many=True,
            allow_none=True,
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
        ),
        name="invoices.returned",
        description="Rows or nothing.",
    )
    # An image result: ``content_kind`` and ``content_mime_type`` change what
    # the tool declares it returns.
    server.register_tool(
        ServiceSpec(
            service=_chart,
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
        ),
        name="invoices.chart",
        description="Draw a chart.",
        content_kind=ToolContentKind.IMAGE,
        content_mime_type="image/png",
    )
    # An MCP Apps tool: ``_meta.ui`` names a registered resource and who may
    # see the tool, ``unknown_arguments`` loosens the input schema, and
    # ``include_output_schema=False`` drops the output schema.
    server.register_ui_resource(name="table", uri="ui://invoices/table.html", html="<h1>x</h1>")
    server.register_tool(
        ServiceSpec(
            service=record_invoice,
            validator=DataclassValidator(InvoiceInput),
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
        ),
        name="invoices.create_loose",
        description="Create an invoice, ignoring unknown arguments.",
        ui=UIToolMeta(resource_uri="ui://invoices/table.html", visibility=[UIVisibility.APP]),
        unknown_arguments=UnknownArguments.IGNORE,
        include_output_schema=False,
    )
    # A bulk service: ``many`` wraps the input and the output in arrays, the
    # input under ``items`` and bounded by ``many_min_length`` and
    # ``many_max_length``.
    server.register_tool(
        ServiceSpec(
            service=record_invoice,
            validator=DataclassValidator(InvoiceInput),
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
            many=True,
            many_min_length=1,
            many_max_length=20,
        ),
        name="invoices.create_many",
        description="Create several invoices.",
    )
    # A Validator declaring itself per transport: the listing reads it under the
    # ``mcp`` stamp, so ``session_ref`` is asked for and ``operator`` is not.
    server.register_tool(
        ServiceSpec(
            service=record_invoice,
            validator=_PerTransport(),
            presenter=DataclassPresenter(InvoiceOutput),
            permissions=[Unrestricted()],
        ),
        name="invoices.create_per_transport",
        description="Create an invoice, naming the session it came from.",
    )
    return server


def _listing() -> dict[str, Any]:
    """Every page of ``tools/list``, as the endpoint encodes it."""
    tools: list[dict[str, Any]] = []
    with override_settings(ROOT_URLCONF=urlconf_for(_server())):
        client = Client()
        initialized = post_jsonrpc(
            client,
            method="initialize",
            params={
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0.0"},
            },
            protocol_version=None,
        )
        session_id = initialized["Mcp-Session-Id"]
        params: dict[str, Any] = {}
        while True:
            response = post_jsonrpc(
                client, method="tools/list", params=params, session_id=session_id
            )
            assert response.status_code == 200, response.content
            result = json.loads(response.content)["result"]
            tools.extend(result["tools"])
            if not result.get("nextCursor"):
                return {"tools": tools}
            params = {"cursor": result["nextCursor"]}


def _encode(listing: dict[str, Any]) -> str:
    """The dump's text: sorted keys, two-space indent, one trailing newline."""
    return json.dumps(listing, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_the_advertised_tool_schemas_match_the_checked_in_dump():
    current = _encode(_listing())
    if WRITE:
        DUMP.write_text(current, encoding="utf-8")
        # A skip, not a pass: a ``TOOL_SCHEMAS_WRITE=1`` left exported must not
        # turn drift into a silent rewrite that CI reports green.
        pytest.skip("wrote tests/testapp/tool_schemas.json")
    checked_in = DUMP.read_text(encoding="utf-8") if DUMP.exists() else ""
    if current != checked_in:
        diff = "".join(
            difflib.unified_diff(
                checked_in.splitlines(keepends=True),
                current.splitlines(keepends=True),
                fromfile="tests/testapp/tool_schemas.json (checked in)",
                tofile="tools/list (now)",
            )
        )
        lines = diff.splitlines(keepends=True)
        shown = "".join(lines[:200]) + (
            f"... {len(lines) - 200} more lines\n" if len(lines) > 200 else ""
        )
        raise AssertionError(
            "The advertised tool schemas changed. If the change is intended, run "
            "`make tool-schemas` and commit tests/testapp/tool_schemas.json in the "
            f"same pull request.\n{shown}"
        )


def test_every_advertised_output_schema_is_an_object():
    # MCP 2025-11-25 declares ``outputSchema.type: "object"``, so a list a
    # tool presents travels under ``items`` rather than as the root. Read off
    # the checked-in dump, which the test above holds to the wire.
    listing = json.loads(DUMP.read_text(encoding="utf-8"))
    advertised = {
        tool["name"]: tool["outputSchema"]["type"]
        for tool in listing["tools"]
        if "outputSchema" in tool
    }

    assert advertised
    assert {name: kind for name, kind in advertised.items() if kind != "object"} == {}
