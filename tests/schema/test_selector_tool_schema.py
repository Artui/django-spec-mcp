"""A kernel selector tool's ``inputSchema``: its reads and shapes, as declared.

A kernel selector declares what it takes: ``reads`` for what selects the rows
and ``shapes`` for what shapes how each is presented, such as a django-restql
``query``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.schema.agent_conventions import PAGED_QUERY_PARAM_SCOPE

OPEN = [Unrestricted()]


@dataclass
class _Widget:
    pk: int


_QUERY = Parameter("query", "string", help="django-restql fieldset, e.g. {id, name}")


def _schema(spec: Any, **knobs: Any) -> dict[str, Any]:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(spec, name="t", description="A tool.", **knobs)
    listed: Any = server.list_tools(user=None)
    (tool,) = listed["tools"]
    return tool["inputSchema"]


def _selector(kind: SelectorKind, **declared: Any) -> SelectorSpec:
    return SelectorSpec(
        kind=kind,
        selector=lambda **_: [],
        presenter=DataclassPresenter(_Widget),
        permissions=OPEN,
        **declared,
    )


def test_a_retrieves_reads_are_its_arguments() -> None:
    # Nothing is read off the selector's signature, so a
    # selector taking ``user`` and ``pk`` asks for what its reads declare.
    spec = _selector(
        SelectorKind.RETRIEVE, reads=Parameters([Parameter("pk", "integer", required=True)])
    )

    assert _schema(spec) == {
        "type": "object",
        "properties": {"pk": {"type": "integer"}},
        "required": ["pk"],
        "additionalProperties": False,
    }


# ---------- what a shape applies to on a page ----------


def test_a_paged_tool_says_its_shape_applies_to_each_item() -> None:
    """The ``outputSchema`` shows the envelope, so the shape has to say it is not it."""
    schema = _schema(_selector(SelectorKind.LIST, shapes=Parameters([_QUERY])), paginate=True)

    assert schema["properties"]["query"] == {
        "type": "string",
        "description": f"django-restql fieldset, e.g. {{id, name}}. {PAGED_QUERY_PARAM_SCOPE}",
    }


def test_the_sentence_stands_alone_when_nothing_was_declared() -> None:
    bare = Parameter("fields", "string")

    schema = _schema(_selector(SelectorKind.LIST, shapes=Parameters([bare])), paginate=True)

    assert schema["properties"]["fields"]["description"] == PAGED_QUERY_PARAM_SCOPE


def test_a_paged_tools_reads_are_advertised_as_declared() -> None:
    # A read selects rows, so it applies to the call rather than to each item.
    spec = _selector(SelectorKind.LIST, reads=Parameters([_QUERY]))

    schema = _schema(spec, paginate=True)

    assert schema["properties"]["query"]["description"] == "django-restql fieldset, e.g. {id, name}"


def test_an_unpaginated_list_advertises_the_shape_as_declared() -> None:
    schema = _schema(_selector(SelectorKind.LIST, shapes=Parameters([_QUERY])))

    assert schema["properties"]["query"]["description"] == "django-restql fieldset, e.g. {id, name}"


def test_a_retrieve_advertises_the_shape_as_declared() -> None:
    schema = _schema(_selector(SelectorKind.RETRIEVE, shapes=Parameters([_QUERY])))

    assert schema["properties"]["query"]["description"] == "django-restql fieldset, e.g. {id, name}"


@dataclass
class _Touched:
    pk: int


def test_a_service_tool_advertises_the_shape_as_declared() -> None:
    spec = ServiceSpec(
        service=lambda: _Touched(pk=1),
        shapes=Parameters([_QUERY]),
        presenter=DataclassPresenter(_Touched),
        atomic=False,
        permissions=OPEN,
    )

    schema = _schema(spec)

    assert schema["properties"]["query"]["description"] == "django-restql fieldset, e.g. {id, name}"
    assert PAGED_QUERY_PARAM_SCOPE not in json.dumps(schema)
