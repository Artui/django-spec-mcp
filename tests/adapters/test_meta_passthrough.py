"""The generic ``_meta`` bundle is threaded registration to binding to wire, for kernel specs.

``_meta`` is the base protocol's open extension namespace. This package carries
it verbatim: nothing here validates, reserves, or interprets a key. The
registration parameter is spelled ``meta`` and lands on the binding as the single
source of truth and on the listing payload. Asked here of the kernel route:
``register_tool``, ``register_tools(overrides=)`` and the kernel form of
``register_resource``, through ``resources/templates/list`` and the
``contents`` block of a read, sync and async; and of prompts, which are no spec
and register the same way on either route.

:func:`merge_meta` is the one place contributions are combined, so a feature
that needs to inject its own key adds an argument at the adapter call site
rather than touching every binding.

The drf-services original also asked it of the ``service_tool`` /
``selector_tool`` / ``resource`` decorators and of ``ToolDefinition`` entries,
which build drf-services specs and have no kernel counterpart: a kernel spec is
declared once and registered, and ``register_tools(overrides=)`` is the bulk
form. It is kept beside this file as ``test_meta_passthrough_drfs.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest
from django_service_specs import (
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.adapters.utils import merge_meta
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_prompts_list import handle_prompts_list
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.handle_resources_templates_list import (
    handle_resources_templates_list,
)
from django_spec_mcp.handlers.handle_tools_list import handle_tools_list
from django_spec_mcp.handlers.types.context import MCPCallContext

UI_META: dict[str, Any] = {"example.com/panel": {"href": "panel://invoices"}}


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def _entry(payload: Any, key: str, name: str) -> dict[str, Any]:
    return next(item for item in payload[key] if item["name"] == name)


def _service() -> ServiceSpec:
    return ServiceSpec(service=lambda: {}, atomic=False, permissions=[Unrestricted()])


def _list() -> SelectorSpec:
    return SelectorSpec(kind=SelectorKind.LIST, selector=lambda: [], permissions=[Unrestricted()])


def _thing() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, pk: {"pk": pk},
        permissions=[Unrestricted()],
        reads=Parameters([Parameter("pk", "string", required=True)]),
    )


def _resource(meta: dict[str, Any] | None = None) -> MCPServer:
    server = _server()
    server.register_resource(_thing(), name="thing", uri_template="things://{pk}", meta=meta)
    return server


# ---------- merge_meta (unit) ----------


def test_merge_meta_with_no_pieces_is_empty() -> None:
    assert merge_meta() == {}


def test_merge_meta_skips_none_and_empty_pieces() -> None:
    assert merge_meta(None, {}, {"a": 1}, None) == {"a": 1}


def test_merge_meta_later_piece_wins_on_a_conflicting_key() -> None:
    assert merge_meta({"a": 1, "b": 2}, {"a": 9}) == {"a": 9, "b": 2}


def test_merge_meta_is_shallow_and_replaces_a_nested_bundle_outright() -> None:
    # Extension keys are opaque bundles owned by one extension: deep-merging two
    # would synthesise a shape neither owner declared.
    assert merge_meta({"ext": {"a": 1, "b": 2}}, {"ext": {"a": 9}}) == {"ext": {"a": 9}}


def test_merge_meta_does_not_mutate_its_inputs() -> None:
    first: dict[str, Any] = {"a": 1}

    merged = merge_meta(first, {"b": 2})

    assert first == {"a": 1}
    assert merged is not first


# ---------- tools ----------


@pytest.mark.parametrize("spec", [_service(), _list()], ids=["service", "selector"])
def test_a_kernel_tool_s_meta_reaches_binding_and_tools_list(spec: Any) -> None:
    server = _server()

    binding = server.register_tool(spec, name="things", meta=UI_META)

    assert binding.meta == UI_META
    assert _entry(handle_tools_list(None, _ctx(server)), "tools", "things")["_meta"] == UI_META


def test_a_tool_without_meta_emits_no_meta_key() -> None:
    server = _server()

    binding = server.register_tool(_service(), name="things.plain")

    assert binding.meta == {}
    assert "_meta" not in _entry(handle_tools_list(None, _ctx(server)), "tools", "things.plain")


def test_meta_and_annotations_are_independent_bundles() -> None:
    server = _server()
    server.register_tool(
        _service(), name="things.both", annotations={"destructiveHint": False}, meta=UI_META
    )

    entry = _entry(handle_tools_list(None, _ctx(server)), "tools", "things.both")

    assert entry["annotations"] == {"readOnlyHint": False, "destructiveHint": False}
    assert entry["_meta"] == UI_META


def test_a_registry_override_carries_meta_to_one_entry() -> None:
    registry = SpecRegistry()
    registry.register("bulk.own", _list())
    registry.register("bulk.plain", _list())

    own, plain = _server().register_tools(registry, overrides={"bulk.own": {"meta": UI_META}})

    assert own.meta == UI_META
    assert plain.meta == {}


# ---------- resources ----------


def test_resource_template_meta_reaches_templates_list() -> None:
    server = _resource(UI_META)

    payload = handle_resources_templates_list(None, _ctx(server))

    assert _entry(payload, "resourceTemplates", "thing")["_meta"] == UI_META


@pytest.mark.django_db
def test_resource_meta_rides_on_the_resources_read_contents_block() -> None:
    out: Any = handle_resources_read({"uri": "things://7"}, _ctx(_resource(UI_META)))

    assert out["contents"][0]["_meta"] == UI_META


@pytest.mark.django_db(transaction=True)
async def test_async_resources_read_carries_the_same_meta() -> None:
    # The async read handler is a parallel implementation: keep it in step.
    out: Any = await handle_resources_read_async({"uri": "things://7"}, _ctx(_resource(UI_META)))

    assert out["contents"][0]["_meta"] == UI_META


@pytest.mark.django_db
def test_a_resource_without_meta_emits_no_meta_key_anywhere() -> None:
    server = _resource()

    templates = handle_resources_templates_list(None, _ctx(server))
    out: Any = handle_resources_read({"uri": "things://7"}, _ctx(server))

    assert "_meta" not in _entry(templates, "resourceTemplates", "thing")
    assert "_meta" not in out["contents"][0]


@pytest.mark.django_db
def test_meta_is_passed_through_verbatim_without_key_validation() -> None:
    # No key is reserved, rejected, or rewritten at this layer.
    weird: dict[str, Any] = {
        "": None,
        "modelcontextprotocol.io/x": [1, 2],
        "9": {"deep": {"er": 1}},
    }

    out: Any = handle_resources_read({"uri": "things://7"}, _ctx(_resource(weird)))

    assert out["contents"][0]["_meta"] == weird


# ---------- prompts ----------


def test_prompt_meta_reaches_prompts_list() -> None:
    server = _server()

    binding = server.register_prompt(name="greet", render=lambda **_: "hi", meta=UI_META)

    assert binding.meta == UI_META
    assert _entry(handle_prompts_list(None, _ctx(server)), "prompts", "greet")["_meta"] == UI_META


def test_a_prompt_without_meta_emits_no_meta_key() -> None:
    server = _server()
    server.register_prompt(name="greet", render=lambda **_: "hi")

    assert "_meta" not in _entry(handle_prompts_list(None, _ctx(server)), "prompts", "greet")


# ---------- isolation ----------


def test_each_binding_gets_its_own_meta_dict() -> None:
    # No shared mutable default, and the caller's dict is not aliased.
    server = _server()
    passed: dict[str, Any] = {"a": 1}

    first = server.register_tool(_service(), name="iso.one", meta=passed)
    second = server.register_tool(_service(), name="iso.two")
    first.meta["b"] = 2

    assert passed == {"a": 1}
    assert second.meta == {}


def test_a_resource_binding_does_not_alias_the_callers_meta() -> None:
    passed: dict[str, Any] = {"a": 1}
    server = _server()

    binding = server.register_resource(
        _thing(), name="thing", uri_template="things://{pk}", meta=passed
    )
    binding.meta["b"] = 2

    assert passed == {"a": 1}


def test_binding_meta_is_copied_before_it_reaches_the_wire() -> None:
    # A caller mutating the emitted payload must not corrupt the binding.
    server = _server()
    binding = server.register_tool(_service(), name="copy.me", meta={"a": 1})

    entry = _entry(handle_tools_list(None, _ctx(server)), "tools", "copy.me")
    entry["_meta"]["a"] = 99

    assert binding.meta == {"a": 1}
