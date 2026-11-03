"""What ``MCPServer`` puts in the context of an in-process call.

``_context`` decides whether the call is charged its rate limits, which is not visible in a
result, so it is read where the handlers read it: the ``-32005`` a spent limit answers.

An in-process call has no session, so ``_call_context`` takes no ``session_id`` and its span
carries no ``mcp.session.id``; the span and the signature are pinned below.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest
from django_service_specs import ServiceSpec, Unrestricted
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from django_spec_mcp import JsonRpcError, JsonRpcErrorCode, MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore

OPEN = [Unrestricted()]


class _AlwaysDeny:
    """A rate limiter reporting its limit spent, counting how often it was asked."""

    def __init__(self) -> None:
        self.asked = 0

    def consume(self, request: HttpRequest, token: TokenInfo) -> int | None:
        self.asked += 1
        return 17


@pytest.fixture
def exporter() -> Any:
    """An in-memory span exporter for one test; ``test_tracing`` explains the shared provider."""
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    yield exporter
    exporter.clear()


def _server(**tool: Any) -> MCPServer:
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    server.register_tool(
        ServiceSpec(service=lambda: {"ok": True}, permissions=OPEN), name="t.x", **tool
    )
    return server


def _call_span(exporter: InMemorySpanExporter) -> Any:
    (span,) = [s for s in exporter.get_finished_spans() if s.name == "mcp.tools.call"]
    return span


def test_the_in_process_call_context_takes_no_session_id() -> None:
    """Fails while ``_call_context`` still accepts ``session_id``: no in-process entry point
    has one to give, so the parameter only invited a caller to invent a session."""
    server = _server()
    extra: dict[str, Any] = {"session_id": "sess-7"}
    with pytest.raises(TypeError, match="session_id"):
        server._call_context(user=None, **extra)


@pytest.mark.django_db
def test_a_call_without_a_session_carries_no_session_attribute(
    exporter: InMemorySpanExporter,
) -> None:
    """Fails with ``if True:`` in ``span_attrs``: the key would be present holding ``None``."""
    server = _server()
    handle_tools_call({"name": "t.x", "arguments": {}}, server._call_context(user=None))
    assert "mcp.session.id" not in _call_span(exporter).attributes


@pytest.mark.django_db(transaction=True)
async def test_acall_tool_opens_its_span_without_a_session_attribute(
    exporter: InMemorySpanExporter,
) -> None:
    server = _server()
    await server.acall_tool("t.x", {}, user=None)
    assert "mcp.session.id" not in _call_span(exporter).attributes


@pytest.mark.django_db(transaction=True)
async def test_acall_tool_past_its_rate_limit_is_refused_before_the_tool_runs() -> None:
    """Fails with ``enforce_rate_limits=False`` as ``_context``'s default."""
    limiter = _AlwaysDeny()
    server = _server(rate_limits=[limiter])
    out = await server.acall_tool("t.x", {}, user=None)
    assert isinstance(out, JsonRpcError)
    assert (out.code, out.data) == (JsonRpcErrorCode.RATE_LIMITED, {"retryAfter": 17})
    assert limiter.asked == 1


@pytest.mark.django_db
def test_an_in_process_context_is_charged_its_rate_limits() -> None:
    """The sync handler honours the context's flag, which defaults on (the worker passes False)."""
    server = _server(rate_limits=[_AlwaysDeny()])
    context = server._call_context(user=None)
    assert context.enforce_rate_limits is True
    out = handle_tools_call({"name": "t.x", "arguments": {}}, context)
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.RATE_LIMITED
