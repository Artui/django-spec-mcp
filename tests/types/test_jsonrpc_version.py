"""``JSONRPC_VERSION``: the envelope version every message carries."""

from __future__ import annotations

from django_spec_mcp.types.jsonrpc_version import JSONRPC_VERSION


def test_jsonrpc_version_is_2_0() -> None:
    assert JSONRPC_VERSION == "2.0"
