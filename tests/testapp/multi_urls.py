"""Two MCP servers mounted in one project — the multi-instance scenario.

Collaborators are passed explicitly rather than resolved from settings, so each
server is independent of test-suite globals (and of the other server).
"""

from __future__ import annotations

from django.urls import path

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore

internal = MCPServer(
    name="internal-mcp",
    version="2.0.0",
    title="Internal Tools",
    description="Internal tools. Staff only.",
    url_namespace="internal-mcp",
    auth_backend=AllowAnyBackend(),
    session_store=InMemorySessionStore(),
)

public = MCPServer(
    name="public-mcp",
    version="1.0.0",
    url_namespace="public-mcp",
    auth_backend=AllowAnyBackend(),
    session_store=InMemorySessionStore(),
)

urlpatterns = [
    path("internal/mcp/", internal.urls),
    path("public/mcp/", public.urls),
]
