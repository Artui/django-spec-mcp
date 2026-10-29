"""What this package does not carry over from ``djangorestframework-mcp-server``.

Each name below was public there and is gone here on purpose: the bundled
authorization-server pieces (a project runs its own authorization server and
verifies its own tokens through ``MCPAuthBackend``), the transport's own
permission types (the kernel's ``PermissionCheck`` gates a prompt and a view),
the guard against settings
keys a fresh package never had, chain tools (a service spec that calls the
others replaces one), and the extras that only re-exposed another package's.
TOON output went too, and ``OutputFormat`` with it, since one format left
nothing to choose; ``RESOURCE_EDGE_SEEDS`` gave way to the kernel's
``CALL_SEEDS``; and a view's callable source is ``render=``, not ``selector=``,
which every spec in the package means a ``SelectorSpec`` by.

A dropped name left behind fails at import where it is loud, but a dropped name
left in a re-export, a settings default or an extra fails nowhere: it is simply
still there. So each one is asserted absent.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import inspect
import warnings

import pytest
from django_service_specs import ServiceSpec, Unrestricted

import django_spec_mcp
import django_spec_mcp.auth
import django_spec_mcp.auth.backends
import django_spec_mcp.output
import django_spec_mcp.types
from django_spec_mcp.conf import DEFAULTS
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.output.tool_result import build_tool_result
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.server.mcp_server import MCPServer


@pytest.mark.parametrize(
    "module",
    [
        "django_spec_mcp.contrib",
        "django_spec_mcp.auth.backends.django_oauth_toolkit_backend",
        "django_spec_mcp.check_removed_settings",
        "django_spec_mcp.adapters.chain_to_tool",
        "django_spec_mcp.handlers.chain_tool_dispatch",
        "django_spec_mcp.registry.types.chain_context",
        "django_spec_mcp.registry.types.chain_step",
        "django_spec_mcp.registry.types.chain_tool_binding",
        "django_spec_mcp.schema.chain_tool_schema",
        "django_spec_mcp.output.encode_toon",
        "django_spec_mcp.output.utils",
        "django_spec_mcp.types.output_format",
        "django_spec_mcp.types.resource_edge_seeds",
    ],
)
def test_a_dropped_module_is_not_importable(module: str) -> None:
    assert importlib.util.find_spec(module) is None


@pytest.mark.parametrize("name", ["ChainStep", "ChainContext"])
def test_a_dropped_name_leaves_the_root(name: str) -> None:
    assert name not in django_spec_mcp.__all__
    assert not hasattr(django_spec_mcp, name)


@pytest.mark.parametrize(
    "name",
    ["DjangoOAuthToolkitBackend", "UnenforcedAudienceWarning", "MountedAuthorizationServerWarning"],
)
def test_a_dropped_backend_name_is_exported_nowhere(name: str) -> None:
    """The bundled django-oauth-toolkit backend and its warnings are a project's to write."""
    assert not hasattr(django_spec_mcp, name)
    assert not hasattr(django_spec_mcp.auth, name)
    assert not hasattr(django_spec_mcp.auth.backends, name)


@pytest.mark.parametrize(
    "namespace", [django_spec_mcp, django_spec_mcp.types, django_spec_mcp.output]
)
@pytest.mark.parametrize("name", ["OutputFormat", "encode_toon", "RESOURCE_EDGE_SEEDS"])
def test_a_dropped_output_or_seed_name_is_exported_nowhere(namespace: object, name: str) -> None:
    """TOON was the one format besides JSON, so its encoder and the enum choosing
    between them went together; the edge seeds are the kernel's ``CALL_SEEDS``."""
    assert name not in namespace.__all__
    assert not hasattr(namespace, name)


@pytest.mark.parametrize(
    ("callable_", "parameter"),
    [
        (MCPServer.register_tool, "output_format"),
        (build_mcp_config, "default_output_format"),
        (build_tool_result, "output_format"),
    ],
    ids=["register_tool", "build_mcp_config", "build_tool_result"],
)
def test_no_callable_takes_an_output_format(callable_: object, parameter: str) -> None:
    assert parameter not in inspect.signature(callable_).parameters


@pytest.mark.parametrize("record", [KernelToolBinding, MCPConfig])
def test_no_record_carries_an_output_format(record: type) -> None:
    fields = record.__dataclass_fields__
    assert "output_format" not in fields
    assert "default_output_format" not in fields


def test_a_view_s_callable_source_is_render_not_selector() -> None:
    """``selector=`` beside ``register_resource``'s ``SelectorSpec`` read as one."""
    parameters = inspect.signature(MCPServer.register_ui_resource).parameters
    assert "selector" not in parameters
    assert parameters["render"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["render"].default is None


def test_the_server_takes_no_resource_url() -> None:
    """It configured only the default backend, and there is none."""
    assert "resource_url" not in inspect.signature(MCPServer).parameters


def test_the_server_registers_no_chain_tool() -> None:
    assert not hasattr(MCPServer, "register_chain_tool")


def test_the_transport_permission_types_are_gone() -> None:
    """A prompt and a view take the kernel's ``PermissionCheck`` and ``scopes=``.

    So the package's own permission protocol and its two implementations are
    not carried: a check on the principal alone is the instance a spec takes.
    """
    assert importlib.util.find_spec("django_spec_mcp.auth.permissions") is None
    for name in ("MCPPermission", "ScopeRequired", "DjangoPermRequired"):
        assert name not in django_spec_mcp.__all__
        assert not hasattr(django_spec_mcp, name)
        assert not hasattr(django_spec_mcp.auth, name)


@pytest.mark.parametrize(
    "key",
    [
        "DCR_ENABLED",
        "DCR_INITIAL_ACCESS_TOKEN",
        "SIMPLEJWT_ACCESS_COOKIE",
        "RESOURCE_URL",
        "ENFORCE_AUDIENCE",
        "DEFAULT_OUTPUT_FORMAT",
    ],
)
def test_a_dropped_setting_has_no_default(key: str) -> None:
    assert key not in DEFAULTS


def test_a_settings_key_drf_mcp_once_removed_is_not_refused(settings) -> None:
    """A fresh package has no history of these keys, so nothing guards them."""
    settings.SPEC_MCP = {"AUTH_BACKEND": "some.dotted.Path"}
    MCPServer(name="dropped-surface")


def test_a_leftover_default_output_format_is_not_read(settings) -> None:
    """Nothing reads the key, so a project still setting it is neither refused nor
    warned, and its tools answer in JSON whatever it names."""
    settings.SPEC_MCP = {"DEFAULT_OUTPUT_FORMAT": "toon"}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        server = MCPServer(name="dropped-surface")
        server.register_tool(
            ServiceSpec(service=lambda: {"ok": True}, permissions=[Unrestricted()], atomic=False),
            name="t",
            description="d",
        )
        result = server.call_tool("t", {}, user=None)
    assert result.to_dict() == {
        "content": [{"type": "text", "text": '{\n  "ok": true\n}'}],
        "structuredContent": {"ok": True},
    }


@pytest.mark.parametrize("extra", ["jwt", "spectacular", "filter", "oauth", "toon"])
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
