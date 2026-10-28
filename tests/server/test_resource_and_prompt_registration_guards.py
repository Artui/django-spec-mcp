"""Registration-time guards on kernel resources and on prompts.

The URI-template channel is the one caller-controlled name channel in the
package: each variable lands in the selector's pool, so ``notes://{user}`` would
let a URI segment stand in for the authenticated principal. A kernel resource's
template is held to the same reserved names as a tool's, and a
duplicated variable, whose later capture shadows the earlier, is refused too.

The unguarded-resource questions (a resource with no permissions warns,
``REQUIRE_TOOL_PERMISSIONS`` refuses it, a guarded spec is silent) are asked of
a kernel resource in ``tests/handlers/test_kernel_unguarded.py`` and not
restated here. The prompt and view questions build no spec.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp import PromptArgument
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.permissions.scope_required import ScopeRequired
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.registry.types.ui_resource_meta import UIResourceMeta
from django_spec_mcp.server.mcp_server import MCPServer
from django_spec_mcp.server.utils import UnguardedToolWarning


@dataclass
class _Note:
    key: str


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _retrieve(read: str = "key") -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda **reads: _Note(key=str(reads[read])),
        reads=Parameters([Parameter(read, "string", required=True)]),
        permissions=[Unrestricted()],
        presenter=DataclassPresenter(_Note),
    )


def _read(server: MCPServer, uri: str) -> Any:
    request = HttpRequest()
    request.method = "POST"
    context = MCPCallContext(
        http_request=request,
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=server.config.protocol_versions[0],
        config=server.config,
    )
    result: Any = handle_resources_read({"uri": uri}, context)
    return json.loads(result["contents"][0]["text"])


# ---------- prompts and views ----------


def test_unguarded_prompt_warns() -> None:
    server = _server()
    with pytest.warns(UnguardedToolWarning, match="MCP prompt 'p' is registered with no"):
        server.register_prompt(name="p", render=lambda **_: "x")


def test_prompt_binding_permissions_silence_the_warning(recwarn: pytest.WarningsRecorder) -> None:
    server = _server()
    server.register_prompt(
        name="p", render=lambda **_: "x", permissions=[ScopeRequired("prompts:read")]
    )
    assert not [w for w in recwarn if issubclass(w.category, UnguardedToolWarning)]


def test_require_tool_permissions_refuses_an_unguarded_prompt(settings: Any) -> None:
    settings.SPEC_MCP = {"REQUIRE_TOOL_PERMISSIONS": True}
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="MCP prompt 'p'"):
        server.register_prompt(name="p", render=lambda **_: "x")
    assert server.prompts.get("p") is None


def test_an_unguarded_view_is_deliberately_allowed(
    settings: Any, recwarn: pytest.WarningsRecorder
) -> None:
    """A view's content sources cannot read the caller's data, so views stay
    exempt on purpose rather than by oversight."""
    settings.SPEC_MCP = {"REQUIRE_TOOL_PERMISSIONS": True}
    server = _server()
    server.register_ui_resource(name="view", uri="ui://view", html="<p>hi</p>", ui=UIResourceMeta())
    assert server.resources.by_uri_template("ui://view") is not None
    assert not [w for w in recwarn if issubclass(w.category, UnguardedToolWarning)]


def test_a_prompt_argument_list_is_unaffected() -> None:
    """Prompt arguments are a different channel: the ``prompts/get`` seed order
    is what protects them, so a declared name is never refused here."""
    binding = _server().register_prompt(
        name="p",
        render=lambda **_: "x",
        arguments=[PromptArgument(name="topic")],
        permissions=[ScopeRequired("prompts:read")],
    )
    assert binding.arguments[0].name == "topic"


# ---------- URI-template variable names ----------


@pytest.mark.parametrize("reserved", ["user", "request", "data", "instance", "result", "queryset"])
def test_a_template_variable_named_after_a_pool_seed_is_refused(reserved: str) -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="collide with reserved transport keys"):
        server.register_resource(
            _retrieve(), name="notes", uri_template=f"notes://{{{reserved}}}/{{key}}"
        )
    assert server.resources.resolve("notes://victim/1") is None


def test_a_duplicated_template_variable_is_refused() -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="duplicate uri_template variable"):
        server.register_resource(_retrieve(), name="notes", uri_template="notes://{key}/{key}")
    assert server.resources.resolve("notes://a/b") is None


@pytest.mark.parametrize("variable", ["serializer", "page"])
def test_a_name_a_tool_reserves_is_an_ordinary_segment_of_a_resource(variable: str) -> None:
    """A read has no Validator to bind ``serializer`` and no post-fetch pipeline
    to take ``page``, so either is a locator segment the selector receives."""
    server = _server()
    server.register_resource(_retrieve(variable), name="r", uri_template=f"r://{{{variable}}}")
    assert _read(server, "r://csv") == {"key": "csv"}


def test_an_ordinary_template_variable_still_registers() -> None:
    binding = _server().register_resource(
        _retrieve(), name="notes", uri_template="notes://{key}", completions={"key": lambda _: []}
    )
    assert binding.is_template
    assert "key" in binding.completions
