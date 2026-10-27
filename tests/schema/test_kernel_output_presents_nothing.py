"""A kernel tool whose output can present nothing advertises a schema its ``{}`` meets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jsonschema
import pytest
from django_service_specs import (
    DataclassPresenter,
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
