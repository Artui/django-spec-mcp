from __future__ import annotations

import types

from django.urls import path

from django_spec_mcp import MCPServer


def urlconf_for(
    server: MCPServer, *, prefix: str = "mcp/", is_async: bool = False
) -> types.ModuleType:
    """Return a throwaway URL-conf module mounting ``server`` at ``prefix``."""
    module = types.ModuleType("tests.testapp._dynamic_urlconf")
    module.urlpatterns = [path(prefix, server.async_urls if is_async else server.urls)]  # type: ignore[attr-defined]
    return module


__all__ = ["urlconf_for"]
