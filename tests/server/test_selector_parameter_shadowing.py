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

from tests.utils import make_server


@dataclass
class _Row:
    pk: int
    seen: str


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
    server = make_server()
    with pytest.raises(ImproperlyConfigured, match=rf"tool 'rows'.*\['{name}'\].*paginate"):
        server.register_tool(
            _spec(name, required=required), name="rows", description="d", paginate=True
        )
    assert "rows" not in server.tools


def test_a_register_tools_override_that_paginates_is_refused_too() -> None:
    registry = SpecRegistry([RegisteredSpec(name="rows", spec=_spec("page"))])
    server = make_server()
    with pytest.raises(ImproperlyConfigured, match=r"\['page'\]"):
        server.register_tools(registry, overrides={"rows": {"paginate": True, "description": "d"}})
    assert "rows" not in server.tools


def test_an_unpaginated_list_tools_pagination_named_read_reaches_the_selector() -> None:
    server = make_server()
    with warnings.catch_warnings():
        # Unpaginated on purpose; the unbounded-list warning is not the question.
        warnings.simplefilter("ignore")
        server.register_tool(_spec("page"), name="rows", description="d")
    result = server.call_tool("rows", {"page": 3}, user=None)
    assert result.is_error is False
    assert result.structured_content == {"items": [{"pk": 1, "seen": "page=3 limit=0"}]}


def test_a_retrieve_tools_pagination_named_read_registers() -> None:
    server = make_server()
    server.register_tool(
        _spec("page", kind=SelectorKind.RETRIEVE, required=True), name="row", description="d"
    )
    result = server.call_tool("row", {"page": 3}, user=None)
    assert result.structured_content == {"pk": 1, "seen": "page=3"}


def test_a_paginated_list_tool_with_other_reads_still_registers() -> None:
    binding = make_server().register_tool(
        _spec("status"), name="rows", description="d", paginate=True
    )
    assert binding.paginate is True
