"""OpenTelemetry instrumentation around tools/call, resources/read, prompts/get.

Tests use an in-memory ``InMemorySpanExporter`` so they verify the wire shape
(span names, attributes) without needing a real OTel collector.

The tool and the resource are kernel specs registered with ``register_tool`` and
``register_resource``; the service raising ``ServiceError`` is registered the
same way, since a kernel binding is built by the registrar. The prompt,
rejection and no-OTel cases build no spec.
"""

from __future__ import annotations

from typing import Any

import pytest
from django_service_specs import (
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceError,
    ServiceSpec,
    Unrestricted,
)
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from django_spec_mcp import MCPServer, PromptArgument
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.handlers.handle_prompts_get import handle_prompts_get
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    """Install an in-memory span exporter for the duration of one test.

    The OTel API only sets a real :class:`TracerProvider` once per process —
    if a previous test installed one, ``set_tracer_provider`` is a no-op.
    We work around that by clearing the exporter rather than re-installing
    the provider when one is already present.
    """
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.clear()


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _ctx(server: MCPServer) -> Any:
    from django.http import HttpRequest

    from django_spec_mcp.auth.types.token_info import TokenInfo
    from django_spec_mcp.handlers.types.context import MCPCallContext

    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        session_id="sess-42",
    )


OPEN = [Unrestricted()]


@pytest.mark.django_db
def test_tools_call_emits_span(exporter: InMemorySpanExporter) -> None:
    server = _server()
    server.register_tool(ServiceSpec(service=lambda: {"ok": True}, permissions=OPEN), name="t.x")
    handle_tools_call({"name": "t.x", "arguments": {}}, _ctx(server))
    spans = list(exporter.get_finished_spans())
    matching = [s for s in spans if s.name == "mcp.tools.call"]
    assert matching, f"expected mcp.tools.call span; got {[s.name for s in spans]}"
    s = matching[-1]
    assert s.attributes["mcp.binding.name"] == "t.x"
    assert s.attributes["mcp.protocol.version"] == "2025-11-25"
    assert s.attributes["mcp.session.id"] == "sess-42"


def test_resources_read_emits_span_with_uri(exporter: InMemorySpanExporter) -> None:
    server = _server()
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, pk: {"pk": pk},
            reads=Parameters([Parameter("pk", "integer", required=True)]),
            permissions=OPEN,
        ),
        name="r",
        uri_template="r://{pk}",
    )
    handle_resources_read({"uri": "r://7"}, _ctx(server))
    spans = list(exporter.get_finished_spans())
    matching = [s for s in spans if s.name == "mcp.resources.read"]
    assert matching, f"expected mcp.resources.read span; got {[s.name for s in spans]}"
    s = matching[-1]
    assert s.attributes["mcp.binding.name"] == "r"
    assert s.attributes["mcp.resource.uri"] == "r://7"


def test_prompts_get_emits_span(exporter: InMemorySpanExporter) -> None:
    server = _server()
    server.register_prompt(
        name="echo",
        render=lambda *, who: f"hi {who}",
        arguments=[PromptArgument(name="who", required=True)],
    )
    handle_prompts_get({"name": "echo", "arguments": {"who": "world"}}, _ctx(server))
    spans = list(exporter.get_finished_spans())
    matching = [s for s in spans if s.name == "mcp.prompts.get"]
    assert matching
    assert matching[-1].attributes["mcp.binding.name"] == "echo"


def test_validation_rejects_do_not_emit_span(exporter: InMemorySpanExporter) -> None:
    """Cheap input rejections (unknown tool, bad params) don't open a span.

    The span is scoped to dispatch — opening it for every parse error would
    flood traces with no useful info.
    """
    server = _server()
    handle_tools_call({"name": "does.not.exist", "arguments": {}}, _ctx(server))
    handle_resources_read({"uri": "nope://x"}, _ctx(server))
    handle_prompts_get({"name": "missing"}, _ctx(server))
    spans = list(exporter.get_finished_spans())
    assert not [s for s in spans if s.name.startswith("mcp.")], (
        f"unexpected MCP spans on validation rejections: {[s.name for s in spans]}"
    )


# ---------- the no-OTel fallback ----------


def test_span_helper_is_noop_without_otel(monkeypatch) -> None:
    """When ``opentelemetry`` isn't importable, ``span()`` yields a no-op."""
    import django_spec_mcp._compat.tracing as tracing

    monkeypatch.setattr(tracing, "_otel_trace", None)
    with tracing.span("mcp.x", attributes={"a": 1}) as s:
        # All Span-like methods exist and silently no-op.
        s.set_attribute("k", "v")
        s.set_status(None)
        s.record_exception(RuntimeError("boom"))


# ---------- ServiceError exception recording (RECORD_SERVICE_EXCEPTIONS) ----------


def _tools_raising_service_error() -> Any:
    """A registry holding a kernel tool whose service raises ``ServiceError``."""

    def boom() -> None:
        raise ServiceError("nope")

    server = _server()
    server.register_tool(ServiceSpec(service=boom, permissions=OPEN), name="t.boom")
    return server.tools


def _ctx_with_tools(tools: Any) -> Any:
    from django.http import HttpRequest

    from django_spec_mcp.auth.types.token_info import TokenInfo
    from django_spec_mcp.handlers.types.context import MCPCallContext
    from django_spec_mcp.registry.prompt_registry import PromptRegistry
    from django_spec_mcp.registry.resource_registry import ResourceRegistry

    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=tools,
        resources=ResourceRegistry(),
        prompts=PromptRegistry(),
        protocol_version="2025-11-25",
    )


@pytest.mark.django_db
def test_service_error_recorded_when_setting_enabled(
    exporter: InMemorySpanExporter, settings
) -> None:
    """``RECORD_SERVICE_EXCEPTIONS=True`` attaches an exception event to the span."""
    settings.SPEC_MCP = {"RECORD_SERVICE_EXCEPTIONS": True}
    handle_tools_call(
        {"name": "t.boom", "arguments": {}}, _ctx_with_tools(_tools_raising_service_error())
    )
    spans = [s for s in exporter.get_finished_spans() if s.name == "mcp.tools.call"]
    assert spans, "expected mcp.tools.call span"
    events = spans[-1].events
    assert any(e.name == "exception" for e in events), (
        f"expected an exception event on the span; got {[e.name for e in events]}"
    )


@pytest.mark.django_db
def test_service_error_not_recorded_by_default(exporter: InMemorySpanExporter, settings) -> None:
    """Default state: ``ServiceError`` is mapped to JSON-RPC but not recorded."""
    settings.SPEC_MCP = {}
    handle_tools_call(
        {"name": "t.boom", "arguments": {}}, _ctx_with_tools(_tools_raising_service_error())
    )
    spans = [s for s in exporter.get_finished_spans() if s.name == "mcp.tools.call"]
    assert spans
    assert not any(e.name == "exception" for e in spans[-1].events)


@pytest.mark.django_db(transaction=True)
async def test_async_service_error_recorded_when_setting_enabled(
    exporter: InMemorySpanExporter, settings
) -> None:
    from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async

    settings.SPEC_MCP = {"RECORD_SERVICE_EXCEPTIONS": True}
    await handle_tools_call_async(
        {"name": "t.boom", "arguments": {}}, _ctx_with_tools(_tools_raising_service_error())
    )
    spans = [s for s in exporter.get_finished_spans() if s.name == "mcp.tools.call"]
    assert spans
    assert any(e.name == "exception" for e in spans[-1].events)


@pytest.mark.django_db(transaction=True)
async def test_async_service_error_not_recorded_by_default(
    exporter: InMemorySpanExporter, settings
) -> None:
    from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async

    settings.SPEC_MCP = {}
    await handle_tools_call_async(
        {"name": "t.boom", "arguments": {}}, _ctx_with_tools(_tools_raising_service_error())
    )
    spans = [s for s in exporter.get_finished_spans() if s.name == "mcp.tools.call"]
    assert spans
    assert not any(e.name == "exception" for e in spans[-1].events)
