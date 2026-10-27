"""``UI_RESOURCE_MIME_TYPE`` - mime type identifying a resource as an interactive HTML view."""

from __future__ import annotations

UI_RESOURCE_MIME_TYPE: str = "text/html;profile=mcp-app"
"""Mime type identifying a resource as an interactive HTML view. Defined by the
MCP Apps extension; a host that does not implement it sees an ordinary HTML
resource."""
