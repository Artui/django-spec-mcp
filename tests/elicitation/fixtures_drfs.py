"""The drf-services twins of the conftest's kernel tools, kept until the deletion.

The ``_drfs`` tests and ``urls_drfs.py`` import them from here; ``conftest.py``
imports nothing from this module, so the deletion takes this file and leaves the
conftest collectable.
"""

from __future__ import annotations

from typing import Any

import pytest
from django_service_specs import AdditionalInputRequired
from rest_framework import serializers as drf_serializers
from rest_framework_services.types.service_spec import ServiceSpec

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore


class DeleteInput(drf_serializers.Serializer):
    count = drf_serializers.IntegerField()
    confirmed = drf_serializers.BooleanField(required=False, default=False)
    reason = drf_serializers.CharField(required=False, allow_blank=True, default="")


def delete_rows(*, data: dict[str, Any]) -> dict[str, Any]:
    if data["count"] > 100 and not data["confirmed"]:
        raise AdditionalInputRequired(
            f"{data['count']} rows match. Confirm to proceed.",
            schema={"confirmed": {"type": "boolean"}},
        )
    if data["count"] > 1000 and not data["reason"]:
        raise AdditionalInputRequired(
            "Deleting more than 1000 rows needs a reason.",
            schema={"reason": {"type": "string", "title": "Why?"}},
        )
    return {"deleted": data["count"], "confirmed": data["confirmed"], "reason": data["reason"]}


def unspecific_service(*, data: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    """A service that says it needs something without saying what."""
    raise AdditionalInputRequired("This needs something I cannot describe.")


def unrenderable_service(*, data: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    """A service whose schema a client could not render — an author's bug."""
    raise AdditionalInputRequired(
        "Tell me about yourself.",
        schema={"profile": {"type": "object", "properties": {"name": {"type": "string"}}}},
    )


@pytest.fixture
def server() -> MCPServer:
    built = MCPServer(
        name="elicitation-fixture",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
    )
    # ``rows.delete`` is the conftest's ``TOOL``, written out so this module
    # imports nothing from a conftest.
    built.register_service_tool(
        name="rows.delete",
        description="Delete rows.",
        spec=ServiceSpec(service=delete_rows, input_serializer=DeleteInput, atomic=False),
    )
    built.register_service_tool(
        name="rows.vague",
        description="Needs something unnamed.",
        spec=ServiceSpec(service=unspecific_service, input_serializer=DeleteInput, atomic=False),
    )
    built.register_service_tool(
        name="rows.unrenderable",
        description="Asks for a shape no form can hold.",
        spec=ServiceSpec(service=unrenderable_service, input_serializer=DeleteInput, atomic=False),
    )
    return built
