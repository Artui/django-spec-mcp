"""A paginated kernel LIST tool refuses a read its pagination takes away.

``page`` and ``limit`` belong to the read pipeline of a ``LIST`` selector tool
registered with ``paginate=True``: the tool's ``inputSchema`` advertises them as
the page and its size, and the call takes both from the arguments before the
selector runs. A ``reads`` entry of either name registers, is advertised as the
pipeline's integer rather than as declared, and then never receives what the
caller sent, so registration refuses it, through ``register_tool`` and through
``register_tools`` alike.

Unlike drf-services, the kernel path takes the two names only from a tool that
paginates: an unpaginated ``LIST`` tool's read of either name receives the
caller's value as any read does, and so does a ``RETRIEVE`` tool's, which cannot
paginate. Both register, and the first is called here to show it.

The drf-services original asked the same of a reflected selector signature and
is kept beside this file as ``test_selector_parameter_shadowing_drfs.py``. Three
of its questions have no kernel counterpart: a ``QueryParam`` shadowing a
selector parameter, mount-level or from an ``OfflineContract``, since a kernel
spec declares its query values as ``reads`` and nothing reroutes them; a
``**kwargs`` catch-all, since a kernel selector's arguments are its declared
``reads``; and an ``input_serializer`` that lays a pagination-named field back,
since a kernel selector has no Validator.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    RegisteredSpec,
    SelectorKind,
    SelectorSpec,
    SpecRegistry,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend


@dataclass
class _Row:
    pk: int
    seen: str


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _rows(*, page: int = 0, limit: int = 0, status: int = 0) -> list[_Row]:
    return [_Row(pk=1, seen=f"page={page} limit={limit}")]


def _row(*, page: int = 0) -> _Row:
    return _Row(pk=1, seen=f"page={page}")


def _spec(name: str, *, kind: SelectorKind = SelectorKind.LIST, required: bool = False) -> Any:
    return SelectorSpec(
        kind=kind,
        selector=_rows if kind is SelectorKind.LIST else _row,
        reads=Parameters([Parameter(name, "integer", required=required)]),
        permissions=[Unrestricted()],
        presenter=DataclassPresenter(_Row),
    )


@pytest.mark.parametrize("required", [False, True], ids=["defaulted", "required"])
@pytest.mark.parametrize("name", ["page", "limit"])
def test_a_paginated_list_tool_refuses_a_pagination_named_read(name: str, required: bool) -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match=rf"tool 'rows'.*\['{name}'\].*paginate"):
        server.register_tool(
            _spec(name, required=required), name="rows", description="d", paginate=True
        )
    assert "rows" not in server.tools


def test_a_register_tools_override_that_paginates_is_refused_too() -> None:
    registry = SpecRegistry([RegisteredSpec(name="rows", spec=_spec("page"))])
    server = _server()
    with pytest.raises(ImproperlyConfigured, match=r"\['page'\]"):
        server.register_tools(registry, overrides={"rows": {"paginate": True, "description": "d"}})
    assert "rows" not in server.tools


def test_an_unpaginated_list_tools_pagination_named_read_reaches_the_selector() -> None:
    server = _server()
    with warnings.catch_warnings():
        # Unpaginated on purpose; the unbounded-list warning is not the question.
        warnings.simplefilter("ignore")
        server.register_tool(_spec("page"), name="rows", description="d")
    result = server.call_tool("rows", {"page": 3}, user=None)
    assert result.is_error is False
    assert result.structured_content == [{"pk": 1, "seen": "page=3 limit=0"}]


def test_a_retrieve_tools_pagination_named_read_registers() -> None:
    server = _server()
    server.register_tool(
        _spec("page", kind=SelectorKind.RETRIEVE, required=True), name="row", description="d"
    )
    result = server.call_tool("row", {"page": 3}, user=None)
    assert result.structured_content == {"pk": 1, "seen": "page=3"}


def test_a_paginated_list_tool_with_other_reads_still_registers() -> None:
    binding = _server().register_tool(
        _spec("status"), name="rows", description="d", paginate=True
    )
    assert binding.paginate is True
