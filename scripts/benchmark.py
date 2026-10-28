"""Micro-benchmark for ``tools/call`` overhead.

Compares three call paths for the same trivial service callable:

1. **Direct** — call the Python function with already-validated kwargs.
2. **Dispatch** — go through ``handle_tools_call`` (the kernel's shape
   check, permission check, kwarg-pool resolution, presentation,
   ``ToolResult`` shaping) but *not* through the HTTP transport.
3. **HTTP** — full round-trip through the Django test client
   (origin / version / session validation, JSON-RPC parsing, dispatch,
   response serialization).

The numbers tell you where the overhead actually lives — handler-side
work vs. transport boilerplate — so you know whether to optimise the
dispatch pipeline or the transport, if optimisation is ever needed.

Run from the repo root::

    uv run python scripts/benchmark.py

``--iterations`` sets how many calls each sample of the dispatch path times
(the HTTP path times a fifth as many); the default is 5000. The project it
serves is configured here, in the script, so it reads nothing of the test
suite's settings or URLs.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from typing import Any

import django
from django.conf import settings
from django.urls import path

# Configured before anything reads settings. This module is the URL conf, so
# the HTTP path mounts exactly the server built below and nothing else.
settings.configure(
    DEBUG=False,
    SECRET_KEY="benchmark-only",
    ALLOWED_HOSTS=["*"],
    INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth"],
    ROOT_URLCONF=__name__,
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    USE_TZ=True,
    SPEC_MCP={"ALLOWED_ORIGINS": ["http://localhost"]},
)
django.setup()

from django.test import Client  # noqa: E402
from django_service_specs import Parameter, Parameters, ServiceSpec, Unrestricted  # noqa: E402

from django_spec_mcp import MCPServer  # noqa: E402
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend  # noqa: E402
from django_spec_mcp.auth.types.token_info import TokenInfo  # noqa: E402
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call  # noqa: E402
from django_spec_mcp.handlers.types.context import MCPCallContext  # noqa: E402
from django_spec_mcp.transport.in_memory_session_store import (  # noqa: E402
    InMemorySessionStore,
)


def _service(*, n: int) -> dict[str, Any]:
    return {"result": n * 2}


def _build_server() -> MCPServer:
    server = MCPServer(
        name="bench",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
    )
    server.register_tool(
        ServiceSpec(
            service=_service,
            reads=Parameters([Parameter("n", "integer", required=True)]),
            permissions=[Unrestricted()],
        ),
        name="double",
        description="Double n.",
    )
    return server


def _bench(name: str, fn, iterations: int = 5_000) -> tuple[str, float, float]:
    # Warm-up.
    for _ in range(min(500, iterations // 10)):
        fn()
    samples: list[float] = []
    for _ in range(5):
        start: float = time.perf_counter()
        for _ in range(iterations):
            fn()
        samples.append((time.perf_counter() - start) / iterations)
    median: float = statistics.median(samples)
    stdev: float = statistics.stdev(samples) if len(samples) > 1 else 0.0
    return name, median, stdev


_EXPECTED: dict[str, Any] = {"result": 42}


def _expect(path: str, structured: Any) -> None:
    """Refuse to time a path that does not answer the call it is timed on.

    A refused call is a much shorter path than a dispatched one, so a
    benchmark timing it would report an overhead for a dispatch that never ran.
    """
    if structured != _EXPECTED:
        raise RuntimeError(f"{path} answered {structured!r}, not {_EXPECTED!r}")


def _direct() -> None:
    _service(n=21)


def _dispatch_factory(server: MCPServer):
    from django.http import HttpRequest

    request = HttpRequest()
    ctx = MCPCallContext(
        http_request=request,
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )

    def fn() -> Any:
        return handle_tools_call({"name": "double", "arguments": {"n": 21}}, ctx)

    _expect("dispatch", fn().get("structuredContent"))
    return fn


def _http_factory():
    client = Client()
    init_resp = client.post(
        "/mcp/",
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "bench", "version": "0"},
                },
            }
        ),
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
        HTTP_ORIGIN="http://localhost",
    )
    sid: str = init_resp.headers["Mcp-Session-Id"]

    body: str = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "double", "arguments": {"n": 21}},
        }
    )

    def fn() -> Any:
        return client.post(
            "/mcp/",
            body,
            content_type="application/json",
            HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
            HTTP_ORIGIN="http://localhost",
            HTTP_MCP_SESSION_ID=sid,
        )

    _expect("HTTP round-trip", fn().json().get("result", {}).get("structuredContent"))

    return fn


SERVER: MCPServer = _build_server()

# ``server.urls`` is the namespaced ``(patterns, app_name, namespace)`` triple
# ``path()`` mounts directly, as ``admin.site.urls`` does.
urlpatterns = [path("mcp/", SERVER.urls)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--iterations", type=int, default=5_000)
    iterations: int = parser.parse_args().iterations

    direct_name, direct_med, direct_sd = _bench("direct call", _direct, iterations)
    disp_name, disp_med, disp_sd = _bench("dispatch", _dispatch_factory(SERVER), iterations)
    http_name, http_med, http_sd = _bench(
        "HTTP round-trip", _http_factory(), iterations=max(1, iterations // 5)
    )

    print(f"{'path':<22}  {'median (µs)':>12}  {'± stdev':>10}  {'overhead vs direct':>20}")
    print("-" * 70)
    for name, med, sd in [
        (direct_name, direct_med, direct_sd),
        (disp_name, disp_med, disp_sd),
        (http_name, http_med, http_sd),
    ]:
        ratio: str = f"{(med / direct_med):>6.1f}x" if med else "n/a"
        print(f"{name:<22}  {med * 1e6:>12.1f}  {sd * 1e6:>10.1f}  {ratio:>20}")


if __name__ == "__main__":
    main()
