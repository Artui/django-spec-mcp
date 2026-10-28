"""A mounted server whose one tool needs a confirmation, for the HTTP suite."""

from __future__ import annotations

from typing import Any

from django.urls import path
from rest_framework_services.types.service_spec import ServiceSpec

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.elicitation.conftest import TOOL
from tests.elicitation.fixtures_drfs import DeleteInput, delete_rows


def _build() -> MCPServer:
    server = MCPServer(
        name="elicitation-e2e",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
    )
    server.register_service_tool(
        name=TOOL,
        description="Delete rows.",
        spec=ServiceSpec(service=delete_rows, input_serializer=DeleteInput, atomic=False),
    )
    return server


SERVER = _build()

urlpatterns: list[Any] = [path("mcp/", SERVER.urls)]
