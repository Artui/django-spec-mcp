"""URL conf — mounts the MCP server at ``/mcp/`` and its metadata at the origin."""

from __future__ import annotations

from django.urls import path
from invoices.mcp import build_server

server = build_server()

urlpatterns = [
    # ``server.urls`` is the namespaced ``(patterns, app_name, namespace)``
    # triple ``path()`` mounts directly, the ``admin.site.urls`` idiom — it is
    # not an argument for ``include()``.
    path("mcp/", server.urls),
    # RFC 9728 3.1's location for the same document, where a client looks when a
    # challenge carries no pointer. It lives at the origin, so it is the root
    # URLconf's to add, and is built from the backend's ``resource``.
    *server.well_known_urls(),
]
