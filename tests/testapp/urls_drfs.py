"""The drf-services test server mounted at ``mcp/`` (server.urls), for the ``*_drfs.py`` tests."""

from __future__ import annotations

from django.urls import path

from tests.testapp.mcp_drfs import build_server

server = build_server()

urlpatterns = [
    path("mcp/", server.urls),
]
