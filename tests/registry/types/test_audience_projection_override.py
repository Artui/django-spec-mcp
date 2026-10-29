"""A kernel tool's audience projection is its presenter's markings, derived once.

The markings live in the presenter's dataclass, as ``Annotated`` extras on its fields
(``AgentInvoiceOutput``), and ``KernelToolBinding.audience_projection`` reads them with
the kernel's own ``audience_projection_for_spec``, so the projected payload and the
advertised ``outputSchema`` read the same markings.

There is no per-tool ``field_audiences=`` override on registration, because the
markings are a fact of the output rather than of the MCP binding. A tool that
needs what its neighbour hides presents through a dataclass of its own, which is
asked below, and so is the refusal of two labels, which the kernel makes as the
projection is derived.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DataclassPresenter,
    FieldAudience,
    FieldMarking,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from tests.testapp.kernel_shapes import AgentInvoiceOutput


@dataclass
class _LookupInvoiceOutput:
    """What a lookup tool presents: ``sent`` shown, and the handle as its label."""

    id: Annotated[int, FieldMarking.label()]
    number: str
    sent: bool


@dataclass
class _TwoLabels:
    id: Annotated[int, FieldMarking.label()]
    number: Annotated[str, FieldMarking.label()]


def _binding(output: Any = AgentInvoiceOutput) -> KernelToolBinding:
    return KernelToolBinding(
        name="invoices",
        description=None,
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda: [],
            presenter=DataclassPresenter(output),
            permissions=[Unrestricted()],
        ),
    )


def test_the_presenters_markings_decide() -> None:
    projection = _binding().audience_projection
    assert projection.audience("sent") is FieldAudience.HIDDEN
    assert projection.audience("id") is FieldAudience.HANDLE
    assert projection.audience("amount_cents") is FieldAudience.CONTENT
    assert projection.label == "number"


def test_a_tool_that_needs_what_its_neighbour_hides_presents_its_own_dataclass() -> None:
    projection = _binding(_LookupInvoiceOutput).audience_projection
    assert projection.audience("sent") is FieldAudience.CONTENT
    assert projection.label == "id"


def test_two_labels_are_refused() -> None:
    """Registration reads the projection, so a server refuses this at startup
    (``tests/server/test_registration_compiles_the_spec.py``)."""
    binding = _binding(_TwoLabels)
    with pytest.raises(ImproperlyConfigured, match="A record has one name"):
        _ = binding.audience_projection


def test_the_projection_is_derived_once_per_binding() -> None:
    binding = _binding()
    assert binding.audience_projection is binding.audience_projection
