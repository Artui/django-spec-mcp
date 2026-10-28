"""A tool that cannot finish without asking, and the contexts that ask it.

``rows.delete`` is deliberately a two-question service: past 100 rows it wants a
confirmation, past 1000 it also wants a reason. One service therefore exercises
the single-round case, the multi-round case, and the thing multi-round exists
for — that the first answer must survive the second question.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest
from django_service_specs import (
    AdditionalInputRequired,
    DataclassValidator,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore

TOOL: str = "rows.delete"


@dataclass
class DeleteRows:
    """What ``rows.delete`` takes: the count, and the two answers it may ask for.

    Both answers are declared like any argument, with the defaults that make
    them optional, because the answer comes back as an ordinary argument and
    the closed argument set admits only what the spec declares.
    """

    count: int
    confirmed: bool = False
    reason: str = ""


def delete_rows_kernel(*, count: int, confirmed: bool = False, reason: str = "") -> dict[str, Any]:
    """A service that asks to confirm a large delete, and for a reason on a larger one."""
    if count > 100 and not confirmed:
        raise AdditionalInputRequired(
            f"{count} rows match. Confirm to proceed.",
            schema={"confirmed": {"type": "boolean"}},
        )
    if count > 1000 and not reason:
        raise AdditionalInputRequired(
            "Deleting more than 1000 rows needs a reason.",
            schema={"reason": {"type": "string", "title": "Why?"}},
        )
    return {"deleted": count, "confirmed": confirmed, "reason": reason}


def vague_kernel(*, count: int, confirmed: bool = False, reason: str = "") -> dict[str, Any]:  # noqa: ARG001
    """A service that asks for input it cannot describe in a schema."""
    raise AdditionalInputRequired("This needs something I cannot describe.")


def unrenderable_kernel(*, count: int, confirmed: bool = False, reason: str = "") -> dict[str, Any]:  # noqa: ARG001
    """A service that asks for input whose schema no form can render."""
    raise AdditionalInputRequired(
        "Tell me about yourself.",
        schema={"profile": {"type": "object", "properties": {"name": {"type": "string"}}}},
    )


def build_kernel_server() -> MCPServer:
    """The elicitation server: the delete, vague and unrenderable tools, as kernel specs."""
    built = MCPServer(
        name="elicitation-kernel",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
    )
    for name, service, description in (
        (TOOL, delete_rows_kernel, "Delete rows."),
        ("rows.vague", vague_kernel, "Needs something unnamed."),
        ("rows.unrenderable", unrenderable_kernel, "Asks for a shape no form can hold."),
    ):
        built.register_tool(
            ServiceSpec(
                service=service,
                validator=DataclassValidator(DeleteRows),
                permissions=[Unrestricted()],
                atomic=False,
            ),
            name=name,
            description=description,
        )
    return built


def context(
    server: MCPServer,
    *,
    capabilities: dict[str, Any] | None = None,
    protocol_version: str = "2026-07-28",
    user: Any = None,
    **config_overrides: Any,
) -> MCPCallContext:
    """A modern request context that can be asked, unless told otherwise.

    ``capabilities=None`` declares form elicitation — the ordinary case. Pass
    ``{}`` for a client that declared none, which is also what every legacy
    request looks like from a handler's side.
    """
    http_request = HttpRequest()
    http_request.method = "POST"
    return MCPCallContext(
        http_request=http_request,
        token=TokenInfo(user=user),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=protocol_version,
        client_capabilities={"elicitation": {}} if capabilities is None else capabilities,
        config=build_mcp_config(**config_overrides) if config_overrides else server._config,
    )


__all__ = [
    "TOOL",
    "DeleteRows",
    "build_kernel_server",
    "context",
    "delete_rows_kernel",
    "unrenderable_kernel",
    "vague_kernel",
]
