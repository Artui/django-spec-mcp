# django-spec-mcp

[![CI](https://github.com/Artui/django-spec-mcp/workflows/tests/badge.svg)](https://github.com/Artui/django-spec-mcp/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/django-spec-mcp.svg)](https://pypi.org/project/django-spec-mcp/)
[![Python versions](https://img.shields.io/pypi/pyversions/django-spec-mcp.svg)](https://pypi.org/project/django-spec-mcp/)
[![Django versions](https://img.shields.io/pypi/djversions/django-spec-mcp.svg)](https://pypi.org/project/django-spec-mcp/)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](https://github.com/Artui/django-spec-mcp/actions/workflows/tests.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/pypi/l/django-spec-mcp.svg)](https://github.com/Artui/django-spec-mcp/blob/main/LICENSE)

Serve [`django-service-specs`](https://github.com/Artui/django-service-specs)
services and selectors as a [Model Context Protocol](https://modelcontextprotocol.io)
(MCP) server, conforming to MCP **2026-07-28** and **2025-11-25** (Streamable HTTP).

!!! tip "Coming from drf-mcp?"
    This package is `djangorestframework-mcp-server` moved onto the kernel.
    [Moving from drf-mcp](moving-from-drf-mcp.md) lists what a project has to
    change, starting with the live state its first deploy drops.

## What this is

`django-service-specs` is the kernel: a Django project declares each operation
once as a spec. A `ServiceSpec` is a mutation, a side-effecting callable; a
`SelectorSpec` is a read. The spec carries what the operation takes (a
`Validator`, or `reads` declared as `Parameters`), who may run it (its
`permissions`, kernel `PermissionCheck`s), and what it returns (a
`Presenter`), and the kernel's `dispatch` runs it the same way for every
transport.

`django-spec-mcp` is the MCP transport over those specs. It mounts a single
`/mcp` endpoint that speaks Streamable HTTP per the MCP spec, and registers
your specs as MCP tools and resources. The endpoints are plain Django views,
and the unit of registration is the spec, not a view.

## When to use it

Reach for this package when:

- Your project declares its operations as `django-service-specs` specs (or is
  about to) and you want to expose them to an MCP-aware client (Claude Desktop,
  Inspector, Cursor, etc.).
- You want a single, spec-compliant `/mcp` endpoint with pluggable auth
  (an `MCPAuthBackend` for the authorization server you run) and a small
  surface that you can audit.
- You don't want to rewrite your service layer in a parallel framework.

Skip it when you don't need the MCP wire format — call your services directly.

## What ships

- **Tools** — `tools/list`, `tools/call` for `register_tool`, over a kernel
  `ServiceSpec` (mutations) or `SelectorSpec` (reads, with an optional
  `FilterSet`, ordering and pagination). `register_tools` registers every
  spec in a kernel `SpecRegistry`.
- **Resources** — `resources/list`, `resources/templates/list`,
  `resources/read` against a kernel `RETRIEVE` `SelectorSpec`; RFC 6570
  templated URIs whose variables are the spec's reads.
- **Prompts** — `prompts/list`, `prompts/get` against render callables
  returning strings, `PromptMessage`s, or async coroutines.
- **Discovery** — `server/discover`, the 2026-07-28 revision's replacement
  for the `initialize` handshake. Answered without a session, so a modern
  client can lead with it.
- **Argument completion** — `completion/complete` against per-argument
  completers on prompts and resource templates.
- **Pluggable auth** — an `MCPAuthBackend` verifies the bearer token;
  `AllowAnyBackend` (dev only) ships, and `bearer_challenge` builds the 401
  challenge a backend of your own answers with. A tool or resource is guarded by its spec's own kernel `permissions`,
  and `scopes=` at registration adds an OAuth scope gate.
- **RFC 8707 audience binding** through `audience_matches`, which a backend
  calls; **RFC 9728 PRM** served from the configured backend.
- **Per-binding rate limits** — `MCPRateLimit` Protocol with
  `FixedWindowRateLimit`, `SlidingWindowRateLimit`, and
  `TokenBucketRateLimit` implementations shipped.
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
pip install django-spec-mcp                              # base install
pip install "django-spec-mcp[redis]"                     # +Redis SSE broker for multi-worker ASGI
pip install "django-spec-mcp[otel]"                      # +OpenTelemetry instrumentation
pip install "django-spec-mcp[redis,otel]"                # everything
```

…or with `uv`:

```bash
uv add django-spec-mcp                                   # base install
uv add "django-spec-mcp[redis]"                          # +Redis SSE broker for multi-worker ASGI
uv add "django-spec-mcp[otel]"                           # +OpenTelemetry instrumentation
uv add "django-spec-mcp[redis,otel]"                     # everything
```

Optional extras degrade gracefully:

- `RedisSSEBroker` / `RedisSSEReplayBuffer` raise a clear `ImportError` from
  `__init__` if `redis` isn't installed.
- The OTel tracing helper yields a no-op span when `opentelemetry-api` isn't
  importable, so handlers stay branch-free.
- `filter_set=` on a selector spec needs `django-filter`, which the kernel's
  `filter` extra installs; nothing else here imports it.

## Where to next

- [Quickstart](quickstart.md) — copy-pasteable end-to-end recipe.
- [Moving from drf-mcp](moving-from-drf-mcp.md) — what a project on
  `djangorestframework-mcp-server` changes to move across.
- [Concepts](concepts.md) — tools vs resources, sessions, tool results, origin
  allowlist.
- [Authentication](auth.md) — backends, permissions, RFC 9728 PRM, RFC 8707
  audience binding.
- [Recipes](recipes/index.md) — opinionated cookbook entries.
- [Reference](reference/index.md) — autodocs for `MCPServer` and the protocol
  types.
