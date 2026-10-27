"""A kernel tool's ``outputSchema``: where the agent projection lands for each kind.

The schema is the kernel's ``spec_output_schema`` under the binding's
projection, which ``kernel_tool_output_schema`` asks for and the listing serves;
the handle wording is this transport's, the server's
``AgentConventions.handle_field_description``. The drf-services original, which
asked the same of ``build_output_schema`` over serializers, is kept beside this
file as ``test_agent_output_schema_drfs.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from django_service_specs import (
    DataclassPresenter,
    FieldMarking,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import AgentConventions, MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.schema.agent_conventions import HANDLE_DESCRIPTION
from tests.testapp.kernel_shapes import InvoiceOutput


@dataclass
class _AgentInvoice:
    id: Annotated[int, FieldMarking.handle("Invoice handle.")]
    number: Annotated[str, FieldMarking.label()]
    sent: Annotated[bool, FieldMarking.hidden()]


@dataclass
class _Handles:
    ref: Annotated[str, FieldMarking.handle()]
    described: Annotated[str, FieldMarking.handle("A widget handle.")]


def _output_schema(
    presented: type | None,
    *,
    kind: SelectorKind = SelectorKind.RETRIEVE,
    paginate: bool = False,
    conventions: AgentConventions | None = None,
) -> Any:
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=None, conventions=conventions
    )
    server.register_tool(
        SelectorSpec(
            kind=kind,
            selector=lambda: [],
            presenter=None if presented is None else DataclassPresenter(presented),
            permissions=[Unrestricted()],
        ),
        name="t",
        description="Read.",
        paginate=paginate,
    )
    listed: Any = server.list_tools(user=None)
    return listed["tools"][0].get("outputSchema")


def test_retrieve_schema_is_annotated_directly() -> None:
    schema = _output_schema(_AgentInvoice)

    assert "sent" not in schema["properties"]
    assert schema["properties"]["id"]["description"] == "Invoice handle."


def test_list_schema_is_annotated_inside_the_array() -> None:
    schema = _output_schema(_AgentInvoice, kind=SelectorKind.LIST)

    assert schema["type"] == "array"
    assert "sent" not in schema["items"]["properties"]


def test_paginated_schema_is_annotated_inside_the_envelope() -> None:
    """The envelope's own keys belong to the paging, not to a presenter."""
    schema = _output_schema(_AgentInvoice, kind=SelectorKind.LIST, paginate=True)
    item = schema["properties"]["items"]["items"]

    assert "sent" not in item["properties"]
    assert set(schema["properties"]) == {"items", "page", "totalPages", "hasNext"}
    assert schema["required"] == ["items", "page", "totalPages", "hasNext"]


def test_an_unmarked_presenter_is_described_in_full() -> None:
    schema = _output_schema(InvoiceOutput)

    assert set(schema["properties"]) == {"id", "number", "amount_cents", "sent"}
    assert all("description" not in fragment for fragment in schema["properties"].values())


def test_no_presenter_yields_no_schema() -> None:
    # The kernel twin of "no output serializer": a spec that declares nothing
    # about its result is advertised with no ``outputSchema`` at all.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(
        ServiceSpec(service=lambda: None, atomic=False, permissions=[Unrestricted()]),
        name="t",
        description="Do.",
    )
    listed: Any = server.list_tools(user=None)

    assert "outputSchema" not in listed["tools"][0]


def test_an_unlabelled_handle_gets_this_transport_s_wording() -> None:
    """The sentence is a prompt, so this transport supplies it; the kernel holds the markings."""
    schema = _output_schema(_Handles)

    assert schema["properties"]["ref"]["description"] == HANDLE_DESCRIPTION
    assert schema["properties"]["ref"]["description"] == (
        "An opaque identifier. Pass it to other tools that ask for one; refer to the "
        "record by its name in anything you say, never by this value."
    )
    # A handle that says what it is keeps its own words.
    assert schema["properties"]["described"]["description"] == "A widget handle."


def test_the_handle_description_is_the_servers() -> None:
    schema = _output_schema(
        _Handles,
        kind=SelectorKind.LIST,
        paginate=True,
        conventions=AgentConventions(handle_field_description="Pass it on."),
    )

    item = schema["properties"]["items"]["items"]
    assert item["properties"]["ref"]["description"] == "Pass it on."
    assert item["properties"]["described"]["description"] == "A widget handle."


def test_none_leaves_an_undescribed_handle_undescribed() -> None:
    schema = _output_schema(_Handles, conventions=AgentConventions(handle_field_description=None))

    assert "description" not in schema["properties"]["ref"]
    assert schema["properties"]["described"]["description"] == "A widget handle."
