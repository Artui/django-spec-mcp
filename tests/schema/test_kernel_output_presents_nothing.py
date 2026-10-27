"""A kernel tool whose output can present nothing advertises a schema its ``{}`` meets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jsonschema
import pytest
from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend

OPEN = [Unrestricted()]


@dataclass
class _Row:
    pk: int
    name: str


@dataclass
class _Loose:
    """No fields, so nothing is required."""


def _server() -> MCPServer:
    server = MCPServer(name="nothing", auth_backend=AllowAnyBackend())
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda: None,
            allow_none=True,
            presenter=DataclassPresenter(_Row),
            permissions=OPEN,
        ),
        name="rows.maybe",
        description="Read a row that may be missing.",
    )
    server.register_tool(
        ServiceSpec(
            service=lambda: None,
            allow_none=True,
            presenter=DataclassPresenter(_Row),
            permissions=OPEN,
        ),
        name="rows.archive",
        description="Archive a row, returning nothing.",
    )
    server.register_tool(
        ServiceSpec(
            service=lambda: {"pk": 1, "name": "a"},
            presenter=DataclassPresenter(_Row),
            permissions=OPEN,
        ),
        name="rows.strict",
        description="Always returns a row.",
    )
    server.register_tool(
        ServiceSpec(
            service=lambda: None,
            allow_none=True,
            presenter=DataclassPresenter(_Loose),
            permissions=OPEN,
        ),
        name="rows.loose",
        description="Nothing required.",
    )
    return server


def _schemas(server: MCPServer) -> dict[str, Any]:
    return {t["name"]: t["outputSchema"] for t in server.list_tools(user=None)["tools"]}


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["rows.maybe", "rows.archive"])
def test_a_result_that_presents_nothing_conforms_to_its_schema(name: str) -> None:
    server = _server()
    schema = _schemas(server)[name]
    served = server.call_tool(name, {}, user=None).to_dict()["structuredContent"]
    assert served == {}
    jsonschema.validate(served, schema)
    assert schema["type"] == "object"
    assert schema["anyOf"] == [{"required": ["pk", "name"]}, {"maxProperties": 0}]
    # Still refuses a half-presented row.
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"pk": 1}, schema)


def test_a_spec_that_always_presents_keeps_its_schema_strict() -> None:
    schema = _schemas(_server())["rows.strict"]
    assert (schema["type"], schema["required"]) == ("object", ["pk", "name"])
    assert "anyOf" not in schema


def test_a_schema_requiring_nothing_takes_no_any_of() -> None:
    schema = _schemas(_server())["rows.loose"]
    assert schema["type"] == "object"
    assert "anyOf" not in schema and not schema.get("required")


# ---------- a list that presents nothing ----------


def _list_server() -> MCPServer:
    server = MCPServer(name="lists", auth_backend=AllowAnyBackend())
    server.register_tool(
        ServiceSpec(
            service=lambda: None,
            returns_many=True,
            allow_none=True,
            presenter=DataclassPresenter(_Row),
            permissions=OPEN,
        ),
        name="rows.returned",
        description="Return the rows written, or nothing.",
    )
    server.register_tool(
        ServiceSpec(
            service=lambda **_: None,
            many=True,
            allow_none=True,
            validator=DataclassValidator(_Row),
            presenter=DataclassPresenter(_Row),
            permissions=OPEN,
        ),
        name="rows.written",
        description="Write rows, returning them or nothing.",
    )
    return server


_LISTS = ["rows.returned", "rows.written"]
_ROW = {"pk": 1, "name": "a"}


def _arguments(name: str) -> dict[str, Any]:
    return {"items": [_ROW]} if name == "rows.written" else {}


@pytest.mark.parametrize("name", _LISTS)
def test_a_list_that_may_present_nothing_is_advertised_as_a_list(name: str) -> None:
    # The kernel writes ``["array", "null"]``, since its dispatch presents the
    # ``None`` the service returned. No MCP result can carry a ``null``: a
    # value that presents nothing is served as structured content of its own,
    # and for a list that is the empty list, which an array schema admits.
    # So the root is ``"array"``, and each row stays as strict as declared.
    schema = _schemas(_list_server())[name]

    assert schema["type"] == "array"
    assert schema["items"]["required"] == ["pk", "name"]
    jsonschema.validate([], schema)
    jsonschema.validate([_ROW], schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate([{"pk": 1}], schema)


@pytest.mark.django_db
@pytest.mark.xfail(
    strict=True,
    reason=(
        "A list-shaped result presenting nothing is still served as {}; serving [] "
        "is the result builder's, outside the schema."
    ),
)
@pytest.mark.parametrize("name", _LISTS)
def test_a_list_that_presents_nothing_conforms_to_its_schema(name: str) -> None:
    server = _list_server()
    schema = _schemas(server)[name]

    served = server.call_tool(name, _arguments(name), user=None).to_dict()["structuredContent"]

    jsonschema.validate(served, schema)
    assert served == []
