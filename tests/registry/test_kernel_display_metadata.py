"""A kernel tool carries ``display_name`` and ``display_description``, and never sends them.

Both are labels an in-process consumer reads, a richer one than the protocol's
``title``, as the drf-services bindings carry them. They reach the binding
through ``register_tool`` and a ``register_tools`` override alike, default to
``None``, and no ``tools/list`` entry carries either.
"""

from __future__ import annotations

from typing import Any

import pytest
from django_service_specs import (
    RegisteredSpec,
    SelectorKind,
    SelectorSpec,
    SpecRegistry,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from tests.testapp.services import list_invoices

_LABELS: dict[str, Any] = {"display_name": "Invoices", "display_description": "Every invoice."}


def _spec() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST, selector=list_invoices, permissions=[Unrestricted()]
    )


def _direct() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(_spec(), name="invoices", description="d", paginate=True, **_LABELS)
    return server


def _bulk() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tools(
        SpecRegistry([RegisteredSpec(name="invoices", spec=_spec())]),
        overrides={"invoices": {"description": "d", "paginate": True, **_LABELS}},
    )
    return server


registrars = pytest.mark.parametrize("build", [_direct, _bulk], ids=["register_tool", "bulk"])


@registrars
def test_the_labels_reach_the_binding(build: Any) -> None:
    binding = build().tools.get("invoices")
    assert (binding.display_name, binding.display_description) == ("Invoices", "Every invoice.")


@registrars
def test_no_listing_carries_them(build: Any) -> None:
    listed: Any = build().list_tools(user=None)
    (tool,) = listed["tools"]
    assert "Invoices" not in repr(tool)
    assert "Every invoice." not in repr(tool)
    assert not {"display_name", "displayName", "display_description"} & set(tool)


def test_both_default_to_none() -> None:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    binding = server.register_tool(_spec(), name="invoices", description="d", paginate=True)
    assert (binding.display_name, binding.display_description) == (None, None)
