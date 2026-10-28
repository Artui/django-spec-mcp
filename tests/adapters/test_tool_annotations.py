"""A kernel tool's MCP annotations are derived from its kind, and a registered hint wins.

A kernel ``SelectorSpec`` registered with ``register_tool`` advertises
``readOnlyHint``; a kernel ``ServiceSpec`` advertises ``destructiveHint`` and,
when it declares ``idempotent``, ``idempotentHint``. A hint passed in
``annotations=`` overrides the derived one. The values are asked of the
``tools/list`` entry, the wire a client reads, beside ``binding.annotations``,
which ``tests/server/test_register_kernel_specs.py`` asks of the binding alone.

The per-tool knob is ``annotations=`` on ``register_tool`` and
``register_tools(overrides=)``, both asked here.
"""

from __future__ import annotations

from typing import Any

import pytest
from django_service_specs import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.adapters.utils import merge_tool_annotations
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _service(**overrides: Any) -> ServiceSpec:
    return ServiceSpec(
        service=lambda: None, atomic=False, permissions=[Unrestricted()], **overrides
    )


def _selector() -> SelectorSpec:
    return SelectorSpec(kind=SelectorKind.LIST, selector=lambda: [], permissions=[Unrestricted()])


def _listed(server: MCPServer, name: str) -> Any:
    listed: Any = server.list_tools(user=None)
    tool = next(t for t in listed["tools"] if t["name"] == name)
    return tool.get("annotations")


# ---------- merge_tool_annotations (unit) ----------


def test_read_only_derives_only_read_only_hint() -> None:
    # destructive / idempotent mean something only when not read-only, so they
    # are deliberately absent here.
    assert merge_tool_annotations(None, read_only=True) == {"readOnlyHint": True}


def test_mutation_derives_destructive_hint() -> None:
    assert merge_tool_annotations(None, read_only=False) == {
        "readOnlyHint": False,
        "destructiveHint": True,
    }


def test_explicit_hints_override_derived_defaults() -> None:
    merged = merge_tool_annotations(
        {"destructiveHint": False, "idempotentHint": True}, read_only=False
    )

    assert merged == {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}


# ---------- on the wire ----------


def test_a_selector_tool_is_read_only() -> None:
    server = _server()
    server.register_tool(_selector(), name="things.list")

    assert _listed(server, "things.list") == {"readOnlyHint": True}


def test_a_service_tool_is_destructive() -> None:
    server = _server()
    server.register_tool(_service(), name="things.create")

    assert _listed(server, "things.create") == {"readOnlyHint": False, "destructiveHint": True}


def test_an_explicit_title_rides_alongside_the_derived_hint() -> None:
    server = _server()
    server.register_tool(_selector(), name="things.list", annotations={"title": "List things"})

    assert _listed(server, "things.list") == {"readOnlyHint": True, "title": "List things"}


@pytest.mark.parametrize("idempotent", [True, False])
def test_a_service_spec_declaring_idempotent_advertises_the_hint(idempotent: bool) -> None:
    # A declared ``False`` is published, not dropped: silence and ``False`` are
    # two answers the kernel keeps apart.
    server = _server()
    server.register_tool(_service(idempotent=idempotent), name="things.upsert")

    assert _listed(server, "things.upsert") == {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": idempotent,
    }


def test_an_undeclared_service_spec_leaves_the_hint_absent() -> None:
    # Holds ``idempotent is not None`` in ``merge_tool_annotations``.
    server = _server()
    server.register_tool(_service(), name="things.create")

    assert "idempotentHint" not in _listed(server, "things.create")


def test_explicit_annotations_win_over_a_declared_idempotent() -> None:
    server = _server()
    server.register_tool(
        _service(idempotent=True),
        name="things.upsert",
        annotations={"idempotentHint": False, "destructiveHint": False},
    )

    assert _listed(server, "things.upsert") == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
    }


def test_a_read_only_tool_never_derives_the_idempotent_hint() -> None:
    # Holds ``not read_only`` in ``merge_tool_annotations``: a selector carries
    # no ``idempotent`` of its own, so the unit call is what can pass one.
    assert merge_tool_annotations(None, read_only=True, idempotent=True) == {"readOnlyHint": True}


def test_a_registry_override_flips_the_hints_of_one_tool() -> None:
    # A per-spec override through the registry reaches the one tool it names.
    registry = SpecRegistry()
    registry.register("things.create", _service())
    registry.register("things.archive", _service())
    server = _server()

    server.register_tools(
        registry, overrides={"things.archive": {"annotations": {"destructiveHint": False}}}
    )

    assert _listed(server, "things.create") == {"readOnlyHint": False, "destructiveHint": True}
    assert _listed(server, "things.archive") == {"readOnlyHint": False, "destructiveHint": False}
