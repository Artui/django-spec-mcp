"""Icons declared at registration reach every listing that can carry them.

The spec attaches ``icons`` to four listable types plus the server's own
``Implementation``. Each is wired separately, so each is asserted separately —
a single spot-check would not have caught the one that was missed.

The tools and resources are kernel specs registered with ``register_tool`` and
``register_resource``. A kernel resource is a ``RETRIEVE`` selector, so the
fixed-URI resource is one reading nothing. The prompt and server cases build no
spec.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.test import override_settings
from django_service_specs import (
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import Icon, IconTheme, MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_initialize import handle_initialize
from django_spec_mcp.handlers.handle_prompts_list import handle_prompts_list
from django_spec_mcp.handlers.handle_resources_list import handle_resources_list
from django_spec_mcp.handlers.handle_resources_templates_list import (
    handle_resources_templates_list,
)
from django_spec_mcp.handlers.handle_tools_list import handle_tools_list
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore

ICON = Icon(src="https://example.test/i.png", mime_type="image/png", sizes=("48x48",))
OPEN = [Unrestricted()]
ICON_DICT: dict[str, Any] = {
    "src": "https://example.test/i.png",
    "mimeType": "image/png",
    "sizes": ["48x48"],
}


def _server(**kwargs: Any) -> MCPServer:
    kwargs.setdefault("name", "t")
    return MCPServer(
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        **kwargs,
    )


def _ctx(server: MCPServer) -> MCPCallContext:
    request = HttpRequest()
    request.method = "POST"
    return MCPCallContext(
        http_request=request,
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=server.config.protocol_versions[0],
        server_info=server._server_info,
        config=server.config,
    )


def test_service_tool_icons_reach_tools_list() -> None:
    server = _server()
    server.register_tool(
        ServiceSpec(service=lambda: {}, permissions=OPEN),
        name="t.svc",
        description="d",
        icons=(ICON,),
    )
    result = handle_tools_list(None, _ctx(server))
    assert result["tools"][0]["icons"] == [ICON_DICT]


def test_selector_tool_icons_reach_tools_list() -> None:
    server = _server()
    server.register_tool(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: None, permissions=OPEN),
        name="t.sel",
        description="d",
        icons=(ICON,),
    )
    result = handle_tools_list(None, _ctx(server))
    assert result["tools"][0]["icons"] == [ICON_DICT]


def test_resource_icons_reach_resources_list() -> None:
    server = _server()
    server.register_resource(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: None, permissions=OPEN),
        name="r",
        uri_template="things://all",
        icons=(ICON,),
    )
    result = handle_resources_list(None, _ctx(server))
    assert result["resources"][0]["icons"] == [ICON_DICT]


def test_template_icons_reach_templates_list() -> None:
    server = _server()
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, pk: None,
            reads=Parameters([Parameter("pk", "integer", required=True)]),
            permissions=OPEN,
        ),
        name="r",
        uri_template="things://{pk}",
        icons=(ICON,),
    )
    result = handle_resources_templates_list(None, _ctx(server))
    assert result["resourceTemplates"][0]["icons"] == [ICON_DICT]


def test_prompt_icons_reach_prompts_list() -> None:
    server = _server()
    server.register_prompt(name="p", render=lambda: "hi", icons=(ICON,))
    result = handle_prompts_list(None, _ctx(server))
    assert result["prompts"][0]["icons"] == [ICON_DICT]


def test_no_icons_means_no_key() -> None:
    """An empty ``icons`` array carries no meaning, so it is omitted entirely."""
    server = _server()
    server.register_prompt(name="p", render=lambda: "hi")
    assert "icons" not in handle_prompts_list(None, _ctx(server))["prompts"][0]


def test_server_icons_and_website_url_reach_initialize() -> None:
    server = _server(icons=(ICON,), website_url="https://example.test/")
    result = handle_initialize({}, _ctx(server))
    info = result.server_info.to_dict()
    assert info["icons"] == [ICON_DICT]
    assert info["websiteUrl"] == "https://example.test/"


def test_server_identity_falls_back_to_settings() -> None:
    """``SERVER_INFO`` carries icons as plain data — dicts, not ``Icon``s."""
    with override_settings(
        SPEC_MCP={
            "SERVER_INFO": {
                "name": "configured",
                "description": "A server, described.",
                "websiteUrl": "https://configured.test/",
                "icons": [{"src": "https://configured.test/i.png", "sizes": ["16x16"]}],
            }
        }
    ):
        server = _server(name=None)
    info = server._server_info.to_dict()
    assert info["description"] == "A server, described."
    assert info["websiteUrl"] == "https://configured.test/"
    assert info["icons"] == [{"src": "https://configured.test/i.png", "sizes": ["16x16"]}]


def test_settings_icons_may_also_be_icon_instances() -> None:
    with override_settings(
        SPEC_MCP={"SERVER_INFO": {"name": "c", "icons": [Icon(src="https://c.test/i.png")]}}
    ):
        server = _server(name=None)
    assert server._server_info.icons == (Icon(src="https://c.test/i.png"),)


def test_settings_theme_round_trips() -> None:
    with override_settings(
        SPEC_MCP={
            "SERVER_INFO": {
                "name": "c",
                "icons": [{"src": "https://c.test/i.png", "theme": IconTheme.LIGHT}],
            }
        }
    ):
        server = _server(name=None)
    assert server._server_info.to_dict()["icons"] == [
        {"src": "https://c.test/i.png", "theme": "light"}
    ]
