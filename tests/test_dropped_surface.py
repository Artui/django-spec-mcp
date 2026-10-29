"""What this package does not carry over from ``djangorestframework-mcp-server``.

Each name below was public there and is gone here on purpose: the bundled
authorization-server pieces (a project runs its own authorization server and
verifies its own tokens through ``MCPAuthBackend``), the guard against settings
keys a fresh package never had, chain tools (a service spec that calls the
others replaces one), and the extras that only re-exposed another package's.

A dropped name left behind fails at import where it is loud, but a dropped name
left in a re-export, a settings default or an extra fails nowhere: it is simply
still there. So each one is asserted absent.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util

import pytest

import django_spec_mcp
from django_spec_mcp.conf import DEFAULTS
from django_spec_mcp.server.mcp_server import MCPServer


@pytest.mark.parametrize(
    "module",
    [
        "django_spec_mcp.contrib",
        "django_spec_mcp.check_removed_settings",
        "django_spec_mcp.adapters.chain_to_tool",
        "django_spec_mcp.handlers.chain_tool_dispatch",
        "django_spec_mcp.registry.types.chain_context",
        "django_spec_mcp.registry.types.chain_step",
        "django_spec_mcp.registry.types.chain_tool_binding",
        "django_spec_mcp.schema.chain_tool_schema",
    ],
)
def test_a_dropped_module_is_not_importable(module: str) -> None:
    assert importlib.util.find_spec(module) is None


@pytest.mark.parametrize("name", ["ChainStep", "ChainContext"])
def test_a_dropped_name_leaves_the_root(name: str) -> None:
    assert name not in django_spec_mcp.__all__
    assert not hasattr(django_spec_mcp, name)


def test_the_server_registers_no_chain_tool() -> None:
    assert not hasattr(MCPServer, "register_chain_tool")


@pytest.mark.parametrize(
    "key", ["DCR_ENABLED", "DCR_INITIAL_ACCESS_TOKEN", "SIMPLEJWT_ACCESS_COOKIE"]
)
def test_a_dropped_setting_has_no_default(key: str) -> None:
    assert key not in DEFAULTS


def test_a_settings_key_drf_mcp_once_removed_is_not_refused(settings) -> None:
    """A fresh package has no history of these keys, so nothing guards them."""
    settings.SPEC_MCP = {"AUTH_BACKEND": "some.dotted.Path"}
    MCPServer(name="dropped-surface")


@pytest.mark.parametrize("extra", ["jwt", "spectacular", "filter"])
def test_a_dropped_extra_is_not_declared(extra: str) -> None:
    declared = importlib.metadata.metadata("django-spec-mcp").get_all("Provides-Extra") or []
    assert extra not in declared


@pytest.mark.parametrize(
    "module",
    [
        "django_spec_mcp.transport.streamable_http_viewset",
        "django_spec_mcp.transport.async_streamable_http_viewset",
        "django_spec_mcp.auth.protected_resource_metadata",
    ],
)
def test_a_viewset_module_is_not_importable(module: str) -> None:
    """The endpoints are plain Django views, in modules named for them."""
    assert importlib.util.find_spec(module) is None


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("django_spec_mcp.transport", "StreamableHttpViewSet"),
        ("django_spec_mcp.transport", "AsyncStreamableHttpViewSet"),
        ("django_spec_mcp.auth", "ProtectedResourceMetadataViewSet"),
    ],
)
def test_a_viewset_name_is_not_exported(module: str, name: str) -> None:
    namespace = importlib.import_module(module)
    assert name not in namespace.__all__
    assert not hasattr(namespace, name)


@pytest.mark.parametrize(
    "view_class_path",
    [
        "django_spec_mcp.transport.StreamableHttpView",
        "django_spec_mcp.transport.AsyncStreamableHttpView",
        "django_spec_mcp.auth.ProtectedResourceMetadataView",
    ],
)
def test_an_endpoint_takes_no_action_map(view_class_path: str) -> None:
    """``as_view`` routes by the HTTP method's own name, so a map is refused."""
    module, _, name = view_class_path.rpartition(".")
    view_class = getattr(importlib.import_module(module), name)
    with pytest.raises(TypeError):
        view_class.as_view({"get": "get"})
    assert not hasattr(view_class, "actions")
    view = view_class.as_view()
    assert view.view_class is view_class
    assert not hasattr(view, "actions")
    assert not hasattr(view, "cls")
