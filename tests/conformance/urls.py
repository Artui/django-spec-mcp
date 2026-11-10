from __future__ import annotations

from typing import Any

from django.urls import path

from tests.conformance.mcp import build_conformance_server


def build_conformance_urls() -> list[Any]:
    server = build_conformance_server()
    return [path("mcp/", server.urls)]


def __getattr__(name: str) -> Any:
    """Build ``urlpatterns`` on each read, so each test resolves a server of its own."""
    if name == "urlpatterns":
        return build_conformance_urls()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
