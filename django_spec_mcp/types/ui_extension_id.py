"""``UI_EXTENSION_ID`` - identifier a client uses to advertise MCP Apps support."""

from __future__ import annotations

UI_EXTENSION_ID: str = "io.modelcontextprotocol/ui"
"""Identifier a client uses to advertise MCP Apps support.

Client to server only: it arrives under ``capabilities.extensions`` in the
``initialize`` request and the spec defines no matching server-side capability. Parsed
for introspection; nothing gates on it (see
[`UIVisibility`][django_spec_mcp.types.UIVisibility]). """
