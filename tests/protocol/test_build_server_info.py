from __future__ import annotations

from typing import Any

import pytest
from django.test import override_settings

from django_spec_mcp.protocol.build_server_info import build_server_info
from django_spec_mcp.version import __version__ as package_version

_CONFIGURED: dict[str, str] = {"name": "configured", "version": "9.9"}


@pytest.mark.parametrize(
    ("server_info", "kwargs", "expected"),
    [
        pytest.param(
            _CONFIGURED,
            {"name": "internal", "version": "1.2.3"},
            ("internal", "1.2.3"),
            id="explicit-values-win-over-settings",
        ),
        # ``SERVER_INFO`` stays the default source, so a project that configures
        # it and never passes ``name=`` keeps its wire identity.
        pytest.param(_CONFIGURED, {}, ("configured", "9.9"), id="omitted-fields-read-the-setting"),
        pytest.param(
            _CONFIGURED,
            {"name": "internal"},
            ("internal", "9.9"),
            id="each-field-falls-back-independently",
        ),
        pytest.param(
            {}, {}, ("django-spec-mcp", package_version), id="an-empty-setting-reads-the-defaults"
        ),
    ],
)
def test_name_and_version_resolve_argument_then_setting_then_default(
    server_info: dict[str, str], kwargs: dict[str, Any], expected: tuple[str, str]
) -> None:
    with override_settings(SPEC_MCP={"SERVER_INFO": server_info}):
        info = build_server_info(**kwargs)
    assert (info.name, info.version) == expected
