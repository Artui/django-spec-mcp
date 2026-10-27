"""``append_agent_conventions`` over a kernel spec's projection.

The projection is the kernel's ``audience_projection_for_spec``, read off the
``FieldMarking`` each presenter field declares in its ``Annotated`` extras,
where the drf-services original read the markings off serializer ``style``.
The original is kept beside this file as ``test_agent_conventions_drfs.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from django_service_specs import (
    DataclassPresenter,
    FieldMarking,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
    audience_projection_for_spec,
)

from django_spec_mcp import AgentConventions, MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.schema.agent_conventions import append_agent_conventions
from tests.testapp.kernel_shapes import InvoiceOutput


@dataclass
class _AgentInvoice:
    """The kernel twin of ``AgentInvoiceSerializer``'s markings."""

    id: Annotated[int, FieldMarking.handle("Invoice handle.")]
    number: Annotated[str, FieldMarking.label()]
    amount_cents: int
    sent: Annotated[bool, FieldMarking.hidden()]


@dataclass
class _Handles:
    id: Annotated[int, FieldMarking.handle()]


def _spec(presented: type) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda: None,
        allow_none=True,
        presenter=DataclassPresenter(presented),
        permissions=[Unrestricted()],
    )


AGENT = audience_projection_for_spec(_spec(_AgentInvoice))


def test_a_tool_with_handles_gains_the_line() -> None:
    result = append_agent_conventions("Fetch an invoice.", AGENT)

    assert result is not None
    assert result.startswith("Fetch an invoice.\n\n")
    assert "Identify records by `number`." in result
    assert result.endswith(
        "pass them to other tools that ask for one; refer to records by their "
        "name in anything you say, never by the identifier."
    )


def test_a_tool_without_handles_is_left_alone() -> None:
    projection = audience_projection_for_spec(_spec(InvoiceOutput))

    assert append_agent_conventions("Fetch an invoice.", projection) == "Fetch an invoice."


def test_handles_without_a_label_still_get_the_line() -> None:
    result = append_agent_conventions(None, audience_projection_for_spec(_spec(_Handles)))

    assert result is not None
    assert "Identify records by" not in result
    assert result.startswith("Fields described as opaque identifiers")


def test_the_line_is_the_one_passed() -> None:
    result = append_agent_conventions(
        "Fetch an invoice.", AGENT, handle_line="Use the ids for calls."
    )

    assert result == "Fetch an invoice.\n\nIdentify records by `number`. Use the ids for calls."


def test_none_drops_the_line_and_its_prefix() -> None:
    assert append_agent_conventions("Fetch an invoice.", AGENT, handle_line=None) == (
        "Fetch an invoice."
    )
    assert append_agent_conventions(None, AGENT, handle_line=None) is None


def _listed_description(conventions: AgentConventions | None) -> Any:
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=None, conventions=conventions
    )
    server.register_tool(_spec(_AgentInvoice), name="get", description="Fetch an invoice.")
    listed: Any = server.list_tools(user=None)
    return listed["tools"][0]["description"]


def test_a_kernel_tools_listing_carries_the_servers_line() -> None:
    # The binding's projection is the spec's, so the listing reads the same
    # markings these unit cases do, in the server's own words.
    described = _listed_description(AgentConventions(handle_line="Use the ids for calls."))

    assert described == "Fetch an invoice.\n\nIdentify records by `number`. Use the ids for calls."
    assert _listed_description(AgentConventions(handle_line=None)) == "Fetch an invoice."
