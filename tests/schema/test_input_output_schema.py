"""A kernel tool's two schemas, by what the spec declares.

The input side is ``kernel_tool_input_schema`` over the kernel's ``spec_input_schema``,
the output side ``kernel_tool_output_schema`` over its ``spec_output_schema``; both are
read here as the listing serves them. What a spec that may present nothing advertises is
``test_kernel_output_presents_nothing.py``'s.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django import forms
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    FormValidator,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend

OPEN = [Unrestricted()]


@dataclass
class _DC:
    name: str
    qty: int = 0


class _NameForm(forms.Form):
    name = forms.CharField()


def _tool(spec: Any, **knobs: Any) -> dict[str, Any]:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(spec, name="t", description="A tool.", **knobs)
    listed: Any = server.list_tools(user=None)
    (tool,) = listed["tools"]
    return tool


def _service(**declared: Any) -> ServiceSpec:
    return ServiceSpec(service=lambda **_: None, atomic=False, permissions=OPEN, **declared)


def _selector(kind: SelectorKind, **declared: Any) -> SelectorSpec:
    return SelectorSpec(
        kind=kind,
        selector=lambda: [],
        presenter=DataclassPresenter(_DC),
        permissions=OPEN,
        **declared,
    )


# ---------- inputSchema ----------


def test_a_spec_declaring_no_arguments_is_a_closed_empty_object() -> None:
    # The kernel's argument set is closed, so a spec declaring none takes no
    # argument at all, rather than an open ``{"type": "object"}``.
    assert _tool(_service())["inputSchema"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


def test_a_dataclass_validators_fields_are_the_arguments() -> None:
    schema = _tool(_service(validator=DataclassValidator(_DC)))["inputSchema"]

    assert schema["properties"]["name"] == {"type": "string"}
    assert schema["properties"]["qty"] == {"type": "integer", "default": 0}
    assert schema["required"] == ["name"]


def test_a_validator_that_is_not_a_dataclass_is_refused_when_built() -> None:
    # The Validator refuses the shape when it is constructed, before any spec
    # holds it, so ``tools/list`` never meets it.
    class NotADataclass:
        pass

    with pytest.raises(ImproperlyConfigured, match="takes a dataclass type"):
        DataclassValidator(NotADataclass)


def test_partial_drops_required() -> None:
    schema = _tool(_service(validator=FormValidator(_NameForm), partial=True))["inputSchema"]

    assert "required" not in schema
    assert schema["properties"]["name"]["type"] == "string"


# ---------- outputSchema ----------


def test_a_retrieve_is_its_item() -> None:
    schema = _tool(_selector(SelectorKind.RETRIEVE))["outputSchema"]

    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"name", "qty"}


def test_an_unpaginated_list_is_an_array_of_its_item() -> None:
    schema = _tool(_selector(SelectorKind.LIST))["outputSchema"]

    assert schema["type"] == "array"
    assert schema["items"]["type"] == "object"
    assert "name" in schema["items"]["properties"]


def test_a_paginated_list_is_the_envelope() -> None:
    schema = _tool(_selector(SelectorKind.LIST), paginate=True)["outputSchema"]

    assert schema["type"] == "object"
    assert schema["properties"]["items"]["type"] == "array"
    assert schema["properties"]["page"] == {"type": "integer"}
    assert schema["properties"]["totalPages"] == {"type": "integer"}
    assert schema["properties"]["hasNext"] == {"type": "boolean"}
    assert schema["required"] == ["items", "page", "totalPages", "hasNext"]


@pytest.mark.parametrize("paginate", [False, True], ids=["array", "envelope"])
def test_a_list_selector_under_allow_none_keeps_its_list_schema(paginate: bool) -> None:
    # A ``LIST`` selector returning ``None`` has returned the empty list, so
    # ``allow_none`` adds nothing to its schema: a list, empty at worst, and the
    # envelope's four keys still required.
    allowing = _tool(_selector(SelectorKind.LIST, allow_none=True), paginate=paginate)
    strict = _tool(_selector(SelectorKind.LIST), paginate=paginate)

    assert allowing["outputSchema"] == strict["outputSchema"]
    assert "anyOf" not in allowing["outputSchema"]
