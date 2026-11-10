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
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.registry.types.ui_resource_meta import UIResourceMeta
from django_spec_mcp.server.mcp_server import MCPServer
from django_spec_mcp.server.utils import UnguardedToolWarning
from tests.utils import make_server


@dataclass
class _Note:
    key: str


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
    server = make_server()
    with pytest.warns(UnguardedToolWarning, match="MCP prompt 'p' is registered with no"):
        server.register_prompt(name="p", render=lambda **_: "x")


@pytest.mark.parametrize(
    "guard",
    [{"permissions": [Unrestricted()]}, {"scopes": "prompts:read"}],
    ids=["permissions", "scopes"],
)
def test_either_guard_silences_the_warning(
    guard: dict[str, Any], recwarn: pytest.WarningsRecorder
) -> None:
    server = make_server()
    server.register_prompt(name="p", render=lambda **_: "x", **guard)
    assert not [w for w in recwarn if issubclass(w.category, UnguardedToolWarning)]


def test_require_tool_permissions_refuses_an_unguarded_prompt(settings: Any) -> None:
    settings.SPEC_MCP = {"REQUIRE_TOOL_PERMISSIONS": True}
    server = make_server()
    with pytest.raises(ImproperlyConfigured, match="MCP prompt 'p'"):
        server.register_prompt(name="p", render=lambda **_: "x")
    assert server.prompts.get("p") is None


def test_an_unguarded_view_is_deliberately_allowed(
    settings: Any, recwarn: pytest.WarningsRecorder
) -> None:
    """A view's content sources cannot read the caller's data."""
    settings.SPEC_MCP = {"REQUIRE_TOOL_PERMISSIONS": True}
    server = make_server()
    server.register_ui_resource(name="view", uri="ui://view", html="<p>hi</p>", ui=UIResourceMeta())
    assert server.resources.by_uri_template("ui://view") is not None
    assert not [w for w in recwarn if issubclass(w.category, UnguardedToolWarning)]


def test_a_prompt_argument_list_is_unaffected() -> None:
    """Prompt arguments are a different channel: prompts/get seed order protects them."""
    binding = make_server().register_prompt(
        name="p",
        render=lambda **_: "x",
        arguments=[PromptArgument(name="topic")],
        scopes="prompts:read",
    )
    assert binding.arguments[0].name == "topic"


# ---------- URI-template variable names ----------


@pytest.mark.parametrize("reserved", ["user", "request", "data", "instance", "result", "queryset"])
def test_a_template_variable_named_after_a_pool_seed_is_refused(reserved: str) -> None:
    server = make_server()
    with pytest.raises(ImproperlyConfigured, match="collide with reserved transport keys"):
        server.register_resource(
            _retrieve(), name="notes", uri_template=f"notes://{{{reserved}}}/{{key}}"
        )
    assert server.resources.resolve("notes://victim/1") is None


def test_a_duplicated_template_variable_is_refused() -> None:
    server = make_server()
    with pytest.raises(ImproperlyConfigured, match="duplicate uri_template variable"):
        server.register_resource(_retrieve(), name="notes", uri_template="notes://{key}/{key}")
    assert server.resources.resolve("notes://a/b") is None


@pytest.mark.parametrize("variable", ["serializer", "page"])
def test_a_name_a_tool_reserves_is_an_ordinary_segment_of_a_resource(variable: str) -> None:
    """A read has no Validator to bind serializer and no pipeline to take page."""
    server = make_server()
    server.register_resource(_retrieve(variable), name="r", uri_template=f"r://{{{variable}}}")
    assert _read(server, "r://csv") == {"key": "csv"}


def test_an_ordinary_template_variable_still_registers() -> None:
    binding = make_server().register_resource(
        _retrieve(), name="notes", uri_template="notes://{key}", completions={"key": lambda _: []}
    )
    assert binding.is_template
    assert "key" in binding.completions
