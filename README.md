# django-spec-mcp

[![CI](https://github.com/Artui/django-spec-mcp/workflows/tests/badge.svg)](https://github.com/Artui/django-spec-mcp/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/django-spec-mcp.svg)](https://pypi.org/project/django-spec-mcp/)
[![Python versions](https://img.shields.io/pypi/pyversions/django-spec-mcp.svg)](https://pypi.org/project/django-spec-mcp/)
[![Django versions](https://img.shields.io/pypi/djversions/django-spec-mcp.svg)](https://pypi.org/project/django-spec-mcp/)
[![Docs](https://img.shields.io/badge/docs-artui.github.io-blue.svg)](https://artui.github.io/django-spec-mcp/)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Artui/django-spec-mcp/gh-pages/coverage.json)](https://github.com/Artui/django-spec-mcp/actions/workflows/tests.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/pypi/l/django-spec-mcp.svg)](LICENSE)

Serve [`django-service-specs`](https://github.com/Artui/django-service-specs)
services and selectors as a [Model Context Protocol](https://modelcontextprotocol.io)
(MCP) server, conforming to MCP **2026-07-28** and **2025-11-25** (Streamable HTTP).

## Idea

Register kernel specs directly. The unit of registration is a `ServiceSpec`
or a `SelectorSpec` from `django-service-specs`, not a view: the spec says what
an operation takes, who may run it and what it returns, and this package serves
it over MCP. The same spec can be served by any other transport on the kernel.

```python
from dataclasses import dataclass

from django.urls import path
from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer


@dataclass
class InvoiceInput:
    number: str
    amount_cents: int


@dataclass
class InvoiceOutput:
    id: int
    number: str
    amount_cents: int


server = MCPServer(name="my-app", auth_backend=my_auth_backend)

server.register_tool(
    ServiceSpec(
        service=create_invoice,  # def create_invoice(*, number, amount_cents)
        validator=DataclassValidator(InvoiceInput),
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[Unrestricted()],
    ),
    name="invoices.create",
    description="Create an invoice.",
)

server.register_resource(
    SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=get_invoice,  # def get_invoice(*, pk)
        reads=Parameters([Parameter("pk", "integer", required=True)]),
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[Unrestricted()],
    ),
    name="invoice",
    uri_template="invoices://{pk}",
)

urlpatterns = [path("mcp/", server.urls)]
```

See the [quickstart](docs/quickstart.md) for the full end-to-end recipe.

- **Services** (mutations) and **selectors** (reads) → MCP **tools**, through
  `register_tool`; the spec's class says which kind of tool it is.
- A `RETRIEVE` selector → an MCP **resource**, through `register_resource`.
- A single `/mcp` endpoint speaks Streamable HTTP. The
  `/.well-known/oauth-protected-resource` endpoint comes mounted alongside.

## What ships

- **Tools** — `tools/list`, `tools/call` for `register_tool`, over a kernel
  `ServiceSpec` (mutations) or `SelectorSpec` (reads, with an optional
  `FilterSet`, ordering and pagination). `register_tools(registry,
  overrides=...)` registers every spec in a kernel `SpecRegistry`.
- **Server-supplied arguments** — `server_arguments=ServerArguments(values=...,
  withheld=...)` at registration answers a declared argument for the caller:
  the name leaves the tool's schema, a caller's value for it is dropped and
  logged by name, and the binding is checked when it is registered. Route and
  query values a tool asks for are read from the spec's own `reads`.
- **Resources** — `resources/list`, `resources/templates/list`,
  `resources/read` against a kernel `RETRIEVE` `SelectorSpec`, presented by
  its own presenter; RFC 6570 templated URIs whose variables are the spec's
  reads.
- **Prompts** — `prompts/list`, `prompts/get` against render callables
  returning strings, `PromptMessage`s, or async coroutines.
- **In-process transport surface** — call tools without an HTTP round-trip:
  `MCPServer.call_tool` / `acall_tool` and `list_tools` / `alist_tools`
  drive the same dispatch and permission checks as the wire path, for
  embedding in agent bridges, toolsets, or management commands.
  `unavailable_tools` / `aunavailable_tools` name each tool the listing leaves
  out with the reason, so a bridge can tell its model why one is missing.
- **Tool annotations** — pass `annotations=` at registration (or rely on the
  read/mutation default) to advertise MCP hints like `readOnlyHint` /
  `destructiveHint` on `tools/list`.
- **Generic `_meta`** — pass `meta=` at registration to populate the base
  protocol's free-form `_meta` object on a tool, resource, or prompt's
  listing entry (and on the `contents` of `resources/read`). Passed through
  verbatim, so protocol extensions have somewhere to live.
- **Interactive views (MCP Apps)** — `register_ui_resource(...)` declares an
  HTML view with typed CSP / permission metadata; `ui=UIToolMeta(...)` on a
  tool links its result to that view, and a host renders it inline in the chat.
  The render payload is the `structuredContent` you already emit, and a view's
  own `tools/call`s inherit your auth, permissions and rate limits. An
  extension over base MCP, so no protocol bump. We declare; the host sandboxes
  and renders.
- **Pluggable auth** — an `MCPAuthBackend` verifies the bearer token;
  `AllowAnyBackend` (dev only) ships, and `bearer_challenge` builds the 401
  challenge a backend of your own answers with. A tool or resource is guarded by its spec's own kernel `permissions`, and
  `scopes=` at registration adds an OAuth scope gate.
- **RFC 8707 audience binding** through `audience_matches`, which a backend
  calls; **RFC 9728 PRM** served from the configured backend.
- **Per-binding rate limits** — `MCPRateLimit` Protocol with
  `FixedWindowRateLimit`, `SlidingWindowRateLimit`, and
  `TokenBucketRateLimit` implementations shipped.
- **Output formats** — JSON (default) and TOON (token-oriented;
  optional extra with safe JSON fallback).
- **Async POST/DELETE + GET-side SSE push** — sync `urls` for WSGI,
  `async_urls` for ASGI; `MCPServer.notify(session_id, payload)`
  pushes JSON-RPC frames on the session's SSE stream. Per-worker
  `InMemorySSEBroker` or cross-worker `RedisSSEBroker` (behind
  `[redis]`); `Last-Event-ID` resume via
  `InMemorySSEReplayBuffer` / `RedisSSEReplayBuffer`.
- **OpenTelemetry instrumentation** — `mcp.tools.call`,
  `mcp.resources.read`, `mcp.prompts.get` spans (no-op without the
  `[otel]` extra installed).
- **Origin allowlist + protocol-version validation + session
  lifecycle** per the transport rules of both revisions. `2026-07-28` and later
  declare version, identity and capabilities on every request and hold no
  session; `2025-11-25` and earlier negotiate once through `initialize`. Both
  are served concurrently on one endpoint, because a legacy client has no
  fall-forward mechanism.

## Install

```bash
pip install django-spec-mcp                              # JSON only
pip install "django-spec-mcp[toon]"                      # +TOON encoder
pip install "django-spec-mcp[redis]"                     # +Redis SSE broker for multi-worker ASGI
pip install "django-spec-mcp[otel]"                      # +OpenTelemetry instrumentation
pip install "django-spec-mcp[toon,redis,otel]"           # everything
```

…or with `uv`:

```bash
uv add django-spec-mcp                                   # JSON only
uv add "django-spec-mcp[toon]"                           # +TOON encoder
uv add "django-spec-mcp[redis]"                          # +Redis SSE broker for multi-worker ASGI
uv add "django-spec-mcp[otel]"                           # +OpenTelemetry instrumentation
uv add "django-spec-mcp[toon,redis,otel]"                # everything
```

Optional extras degrade gracefully: TOON falls back to JSON with a runtime
warning if `python-toon` is not installed.

## Try it

Install [mcp-inspector](https://github.com/modelcontextprotocol/inspector) and
point it at your dev server:

```bash
npx @modelcontextprotocol/inspector --url http://localhost:8000/mcp/
```

Inspector lists tools, fills in arguments from the generated JSON Schema, and
walks the OAuth auth flow against your configured Authorization Server.

## Documentation

- [Quickstart](docs/quickstart.md) — copy-pasteable end-to-end.
- [Concepts](docs/concepts.md) — tools vs resources, sessions, output formats.
- [Authentication](docs/auth.md) — backends, permissions, audience binding,
  bring-your-own AS recipe.
- [Recipes](docs/recipes/index.md) — focused cookbook entries.
- [Reference](docs/reference/index.md) — autodocs for every public symbol.

## License

MIT.
