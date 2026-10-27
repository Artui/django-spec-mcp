"""Registering a kernel tool with no description warns (or refuses).

The counterpart to the unguarded warning. Both properties are required for a
tool to be usable by a model (something must gate the call, something must say
what it does), and an undescribed tool otherwise ships to ``tools/list``
indistinguishable from a documented one.

Asked here of kernel specs through ``register_tool`` and ``register_tools``. The
drf-services original asked it of ``register_service_tool`` and
``register_selector_tool`` and is kept beside this file as
``test_undescribed_warning_drfs.py``.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    RegisteredSpec,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
    Unrestricted,
)

from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.server.mcp_server import MCPServer
from django_spec_mcp.server.utils import UndescribedToolWarning
from tests.testapp.services import list_invoices, record_invoice


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _service(service: Any = record_invoice) -> ServiceSpec:
    return ServiceSpec(service=service, permissions=[Unrestricted()])


def _selector() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST, selector=list_invoices, permissions=[Unrestricted()]
    )


def _undescribed(register: Any) -> list[warnings.WarningMessage]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        register()
    return [w for w in caught if w.category is UndescribedToolWarning]


def test_undescribed_service_tool_warns() -> None:
    server = _server()
    with pytest.warns(UndescribedToolWarning, match="'x' is registered with no description"):
        server.register_tool(_service(), name="x")


def test_undescribed_selector_tool_warns() -> None:
    server = _server()
    assert _undescribed(lambda: server.register_tool(_selector(), name="x", paginate=True))


def test_an_undescribed_registry_entry_warns() -> None:
    registry = SpecRegistry([RegisteredSpec(name="x", spec=_service())])
    server = _server()
    (warning,) = _undescribed(lambda: server.register_tools(registry))
    assert "'x'" in str(warning.message)


def test_a_description_silences_the_warning() -> None:
    server = _server()
    assert not _undescribed(
        lambda: server.register_tool(
            _service(), name="x", description="Archive a widget so it stops appearing."
        )
    )


def test_a_register_tools_override_description_silences_it_too() -> None:
    registry = SpecRegistry([RegisteredSpec(name="x", spec=_service())])
    server = _server()
    assert not _undescribed(
        lambda: server.register_tools(registry, overrides={"x": {"description": "Archive it."}})
    )


def test_whitespace_is_not_a_description() -> None:
    """Otherwise ``description=" "`` silences the check while ``tools/list`` stays blank."""
    server = _server()
    with pytest.warns(UndescribedToolWarning):
        server.register_tool(_service(), name="x", description="   \n ")


def test_a_docstring_on_the_service_does_not_count() -> None:
    """No silent fallback: a docstring is written for the next developer, not a model."""

    def documented_service() -> dict[str, Any]:
        """Internal helper. Assumes the caller already checked tenancy."""
        return {}

    server = _server()
    with pytest.warns(UndescribedToolWarning):
        server.register_tool(_service(documented_service), name="x")
    assert server.tools.get("x").description is None


def test_require_tool_descriptions_refuses_registration(settings: Any) -> None:
    settings.SPEC_MCP = {"REQUIRE_TOOL_DESCRIPTIONS": True}
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="no description"):
        server.register_tool(_service(), name="x")
    assert server.tools.get("x") is None


def test_warning_can_be_filtered_by_category() -> None:
    """The dedicated category exists so consumers can target it precisely."""
    server = _server()
    with warnings.catch_warnings():
        warnings.simplefilter("error", UndescribedToolWarning)
        with pytest.raises(UndescribedToolWarning):
            server.register_tool(_service(), name="x")
