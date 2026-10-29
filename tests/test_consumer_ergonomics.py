"""Consumer-reported ergonomics, on a kernel tool: places the package was correct but silent.

Every case here was reported by a first-party integration that lost debugging
hours to it. None was a broken guarantee: each was a convention the package
depended on and enforced nowhere, or a decision it made and never wrote down.

The registration cases ask ``register_tool`` with kernel specs: the
structured-output pair is refused at registration, and an unguarded spec is
refused by default. The guard is the spec's own ``permissions``, a
``PermissionCheck`` list; the scope gate, the kernel tool's ``scopes=``, does
not count as one. The observability cases build no
spec.
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import PermissionCheck, ServiceSpec, Unrestricted

from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.observability import get_logger, session_fingerprint
from django_spec_mcp.server.mcp_server import MCPServer
from django_spec_mcp.server.utils import UnguardedToolWarning


def _spec(**declared: Any) -> ServiceSpec:
    return ServiceSpec(service=lambda: {"ok": True}, **declared)


def _guarded() -> ServiceSpec:
    return _spec(permissions=[Unrestricted()])


class _Staff(PermissionCheck):
    """A check of the project's own, as a spec declares one."""

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return bool(getattr(principal, "is_staff", False))


class TestStructuredOutputCouplingAtRegistration:
    """A spec-violating pair fails at import, not on first traffic."""

    def _server(self, **config_kwargs: object) -> MCPServer:
        return MCPServer(name="t", config=build_mcp_config(**config_kwargs))

    def test_schema_without_content_is_refused_when_registering(self) -> None:
        server = self._server(include_output_schema=True, include_structured_content=False)
        with pytest.raises(ImproperlyConfigured, match="structuredContent"):
            server.register_tool(_guarded(), name="t.x", description="d")

    def test_a_binding_override_can_make_the_global_pair_legal(self) -> None:
        """Why this check cannot live on the global config alone.

        Server-wide "schema on, content off" is legitimate precisely when every
        binding overrides the content back on, so checking the two settings at
        ``build_mcp_config`` time would reject a working configuration.
        """
        server = self._server(include_output_schema=True, include_structured_content=False)

        binding = server.register_tool(
            _guarded(), name="t.ok", description="d", include_structured_content=True
        )

        assert binding.include_structured_content is True

    def test_the_ordinary_pair_registers_cleanly(self) -> None:
        server = self._server(include_output_schema=True, include_structured_content=True)

        server.register_tool(_guarded(), name="t.y", description="d")

        assert "t.y" in server.tools


class TestUnguardedToolsAreRefusedByDefault:
    """The strict default, and the escape hatch that makes it shippable."""

    def test_the_shipped_default_is_strict(self) -> None:
        """Pinned against the settings file, because the suite opts *out*.

        ``tests/conftest_settings.py`` sets this False so the fixtures whose
        subject is not permissions keep working. That override could otherwise
        mask a regression back to permissive, so assert the shipped value here.
        """
        from django_spec_mcp.conf import DEFAULTS

        assert DEFAULTS["REQUIRE_TOOL_PERMISSIONS"] is True

    def test_an_unguarded_tool_is_refused(self) -> None:
        server = MCPServer(name="t", config=build_mcp_config(require_tool_permissions=True))
        with pytest.raises(ImproperlyConfigured, match="declares no permissions"):
            server.register_tool(_spec(), name="t.open", description="d")

    def test_the_error_names_the_way_out_not_the_way_in(self) -> None:
        """The raise and the warning need *opposite* remedies.

        The warning says why the call runs unchecked. Reusing that text on the
        raise tells you to change the setting that just fired, which reads as a
        bug in the framework rather than a problem with your code.
        """
        server = MCPServer(name="t", config=build_mcp_config(require_tool_permissions=True))
        with pytest.raises(ImproperlyConfigured) as excinfo:
            server.register_tool(_spec(), name="t.open", description="d")
        assert "= False" in str(excinfo.value)
        assert "Unrestricted()" in str(excinfo.value)

    def test_a_scope_gate_is_not_a_guard(self) -> None:
        """The scope gate is the transport's; the spec's ``permissions`` are what count."""
        server = MCPServer(name="t", config=build_mcp_config(require_tool_permissions=True))
        with pytest.raises(ImproperlyConfigured, match="declares no permissions"):
            server.register_tool(_spec(), name="t.open", description="d", scopes=("a",))

    @pytest.mark.parametrize("check", [Unrestricted(), _Staff()], ids=["unrestricted", "own"])
    def test_spec_permissions_satisfy_it(self, check: PermissionCheck) -> None:
        """The idiomatic fix: declare on the spec, so every transport sees it."""
        server = MCPServer(name="t", config=build_mcp_config(require_tool_permissions=True))
        with warnings.catch_warnings():
            warnings.simplefilter("error", UnguardedToolWarning)
            binding = server.register_tool(_spec(permissions=[check]), name="t.g", description="d")
        assert binding.unguarded is False

    def test_the_opt_out_downgrades_it_to_a_warning(self) -> None:
        server = MCPServer(name="t", config=build_mcp_config(require_tool_permissions=False))
        with pytest.warns(UnguardedToolWarning, match="REQUIRE_TOOL_PERMISSIONS'\\] is False"):
            binding = server.register_tool(_spec(), name="t.open", description="d")
        # Only the warning earns the spec the per-call grant it runs on.
        assert binding.unguarded is True


class TestObservability:
    """The package logged nothing at all before 0.25.0."""

    def test_a_session_id_is_never_logged_whole(self) -> None:
        full = "s3cr3t-session-identifier-value"
        tag = session_fingerprint(full)
        assert full not in tag
        assert tag.startswith("s3cr3t-s")

    def test_a_missing_session_is_distinguishable_in_the_tag(self) -> None:
        assert session_fingerprint(None) == "-"
        assert session_fingerprint("") == "-"

    def test_the_namespace_is_configurable_as_one_tree(self) -> None:
        """A project silences or raises the whole package with one entry."""
        assert get_logger("django_spec_mcp.transport.x").name.startswith("django_spec_mcp")

    def test_a_rejected_session_names_its_exact_cause_server_side(self, caplog) -> None:
        """The no-oracle rule constrains the response, not the log.

        The wire deliberately merges unknown-id with wrong-principal. An
        operator is not the adversary that protects against, so the log names
        which one fired — the single line that would have ended the incident
        this wave came out of on day one.
        """
        from tests.transport.test_sessionless_legacy import _post_request, _view

        with caplog.at_level(logging.WARNING, logger="django_spec_mcp"):
            _view(sessions=True)(_post_request("ping", session_id="made-up"))
        assert any("session-unknown" in record.getMessage() for record in caplog.records)

    def test_a_missing_session_logs_the_other_cause(self, caplog) -> None:
        from tests.transport.test_sessionless_legacy import _post_request, _view

        with caplog.at_level(logging.WARNING, logger="django_spec_mcp"):
            _view(sessions=True)(_post_request("ping"))
        assert any("session-missing" in record.getMessage() for record in caplog.records)
