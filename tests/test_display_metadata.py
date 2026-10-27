"""``display_name`` / ``display_description`` on a kernel tool: consumer-only metadata.

The two labels are carried from registration onto the binding for an in-process
consumer to read (django-spec-agents' tool catalog reads both off each binding),
and the server never emits them on the wire. They carry onto the kernel path:
a kernel spec has no label field of its own, so they stay registration knobs.

``register_tool`` does not take them yet, so every case here is a strict xfail
until it does. The drf-services original asked the same of
``register_service_tool``, ``register_selector_tool``, ``ToolDefinition`` and the
module ``register_tools``; it is kept beside this file as
``test_display_metadata_drfs.py``. ``ToolDefinition`` has no kernel counterpart:
``register_tools(registry, overrides=)`` takes its place, and is asked here.
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
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend

_NOT_YET = pytest.mark.xfail(
    strict=True,
    raises=TypeError,
    reason=(
        "register_tool takes no display_name / display_description, and KernelToolBinding "
        "carries neither. Fix: add both, as the drfs bindings carry them."
    ),
)


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _service() -> ServiceSpec:
    return ServiceSpec(service=lambda: {}, permissions=[Unrestricted()])


def _selector() -> SelectorSpec:
    return SelectorSpec(kind=SelectorKind.LIST, selector=lambda: [], permissions=[Unrestricted()])


@_NOT_YET
@pytest.mark.parametrize("spec", [_service(), _selector()], ids=["service", "selector"])
def test_a_kernel_tool_carries_display_metadata(spec: Any) -> None:
    labels: dict[str, Any] = {
        "display_name": "Nice Tool",
        "display_description": "Does nice things",
    }

    binding: Any = _server().register_tool(spec, name="t", **labels)

    assert (binding.display_name, binding.display_description) == ("Nice Tool", "Does nice things")


@pytest.mark.xfail(
    strict=True,
    raises=AttributeError,
    reason="KernelToolBinding has no display_name / display_description fields yet.",
)
def test_the_binding_defaults_to_no_display_metadata() -> None:
    binding: Any = _server().register_tool(_service(), name="t")

    assert binding.display_name is None
    assert binding.display_description is None


@_NOT_YET
def test_register_tools_forwards_metadata_from_the_overrides() -> None:
    registry = SpecRegistry()
    registry.register("s", _service())

    bindings: Any = _server().register_tools(
        registry, overrides={"s": {"display_name": "S", "display_description": "sd"}}
    )

    assert (bindings[0].display_name, bindings[0].display_description) == ("S", "sd")


@_NOT_YET
def test_display_metadata_is_not_emitted_on_the_wire() -> None:
    server = _server()
    labels: dict[str, Any] = {"display_name": "Hidden", "display_description": "Secret"}
    server.register_tool(_service(), name="t", **labels)

    listed: Any = server.list_tools(user=None)
    serialized = str(listed["tools"][0])

    for needle in ("display_name", "displayName", "Hidden", "Secret"):
        assert needle not in serialized
