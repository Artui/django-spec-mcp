"""Registering an unpaginated kernel LIST selector warns (or refuses).

The third member of the registration-time family, after the unguarded and the
undescribed warnings, and the one with a production incident behind it: an
unpaginated LIST tool serialises whatever its selector resolves to, which for a
plain ``Model.objects.all()`` is the whole table.

It warns rather than clamping because there is nowhere honest to put the truth:
a paginated result carries ``hasNext`` / ``totalPages``, so a clamped page tells
the model there is more, while a silently shortened unpaginated list looks
complete to the model reading it.

Asked here of kernel specs registered with ``register_tool`` and
``register_tools``, which reach one binding builder. The drf-services original
asked it of ``register_selector_tool`` and is kept beside this file as
``test_unbounded_list_warning_drfs.py``.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    Parameter,
    Parameters,
    RegisteredSpec,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
    Unrestricted,
)

from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.server.mcp_server import MCPServer
from django_spec_mcp.server.utils import UnboundedListWarning


@dataclass
class _Row:
    pk: int


def _server(**config: Any) -> MCPServer:
    return MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=None,
        config=build_mcp_config(**config) if config else None,
    )


def _rows() -> list[_Row]:
    return []


def _row(*, pk: int) -> _Row:
    return _Row(pk=pk)


def _list() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_rows,
        permissions=[Unrestricted()],
        presenter=DataclassPresenter(_Row),
    )


def _unbounded(register: Any) -> list[warnings.WarningMessage]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        register()
    return [w for w in caught if w.category is UnboundedListWarning]


def test_unpaginated_list_tool_warns() -> None:
    server = _server()
    with pytest.warns(UnboundedListWarning, match="registered with paginate=False"):
        server.register_tool(_list(), name="x", description="d")


def test_a_spec_registry_entry_warns_as_a_direct_registration_does() -> None:
    registry = SpecRegistry([RegisteredSpec(name="x", spec=_list())])
    with pytest.warns(UnboundedListWarning, match="'x'"):
        _server().register_tools(registry, overrides={"x": {"description": "d"}})


def test_the_warning_names_both_remedies() -> None:
    server = _server()
    (warning,) = _unbounded(lambda: server.register_tool(_list(), name="x", description="d"))
    message = str(warning.message)
    assert "paginate=True" in message
    assert "REQUIRE_LIST_PAGINATION" in message
    assert "MAX_RESULT_BYTES" in message


def test_paginated_list_tool_is_silent() -> None:
    server = _server()
    assert not _unbounded(
        lambda: server.register_tool(_list(), name="x", description="d", paginate=True)
    )


def test_retrieve_selector_is_exempt() -> None:
    """A RETRIEVE selector returns one instance, bounded by construction."""
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_row,
        reads=Parameters([Parameter("pk", "integer", required=True)]),
        permissions=[Unrestricted()],
        presenter=DataclassPresenter(_Row),
    )
    server = _server()
    assert not _unbounded(lambda: server.register_tool(spec, name="one", description="d"))


def test_a_service_tool_is_exempt() -> None:
    """A service has no selector kind, so the LIST check never reads it."""
    spec = ServiceSpec(
        service=lambda *, data: _Row(pk=1),
        validator=DataclassValidator(_Row),
        permissions=[Unrestricted()],
        presenter=DataclassPresenter(_Row),
    )
    server = _server()
    assert not _unbounded(lambda: server.register_tool(spec, name="make", description="d"))


def test_require_list_pagination_refuses_the_registration() -> None:
    server = _server(require_list_pagination=True)
    with pytest.raises(ImproperlyConfigured, match="registered with paginate=False"):
        server.register_tool(_list(), name="x", description="d")
    assert "x" not in server.tools


def test_require_list_pagination_allows_a_paginated_tool() -> None:
    server = _server(require_list_pagination=True)
    binding = server.register_tool(_list(), name="x", description="d", paginate=True)
    assert binding.paginate is True


def test_the_warning_points_at_the_registering_line_not_this_package() -> None:
    """A warning blames the caller's file, through both registrars.

    ``register_tool`` and ``register_tools`` reach the check at different
    depths (the second through a generator), so a fixed ``stacklevel`` would
    name a frame inside this package for one of them.
    """
    registry = SpecRegistry([RegisteredSpec(name="y", spec=_list())])
    (direct,) = _unbounded(lambda: _server().register_tool(_list(), name="x", description="d"))
    (batch,) = _unbounded(
        lambda: _server().register_tools(registry, overrides={"y": {"description": "d"}})
    )
    assert direct.filename == __file__
    assert batch.filename == __file__
