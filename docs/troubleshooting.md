# Troubleshooting

## Every tool call returns 404

This is the most confusing failure the transport can produce, because four
unrelated causes look identical from the client. Work down the list.

### 1. Read the `MCP-Error` header

Every session rejection carries one:

| Header | HTTP | Meaning |
|---|---|---|
| `MCP-Error: session-missing` | `400` | No `Mcp-Session-Id` arrived at all |
| `MCP-Error: session-unknown` | `404` | An id arrived that this server will not honour |
| *(no header)* | `404` | **Not us.** Something in front of the application answered |

That last row is the important one. A load balancer with no matching rule
returns a bodiless 404 that is indistinguishable from a dead session — and the
JSON-RPC body that would tell them apart often never reaches a human, because
clients commonly log `${status} ${statusText}` and **HTTP/2 has no reason
phrase**. If there is no `MCP-Error` header, look at your routing layer, not at
this package.

!!! warning "Application logs may be silent either way"

    If your logging does not include `django_spec_mcp` (see
    [Observability](observability.md)), the absence of log lines tells you
    *nothing* about whether the request arrived. Configure the logger before
    concluding anything from silence.

### 2. Check whether the session simply expired

`session-unknown` covers expired, evicted, terminated, and minted-for-another-
principal. Sessions have two windows:

- `SESSION_TTL_SECONDS` — **idle** timeout, default 24h, restarted on every
  request. A connector used continuously never hits it.
- `SESSION_MAX_AGE_SECONDS` — **absolute** ceiling, default 7 days, regardless
  of activity.

A connector idle over a long weekend hits the first with nothing wrong anywhere.

### 3. Rule out cache eviction

Neither window can promise more than the cache underneath it. A Redis
`maxmemory-policy` of `allkeys-lru` (or `allkeys-random`) evicts session keys
long before their timeout, and that is **indistinguishable from expiry** from
every angle a client can observe.

```bash
redis-cli CONFIG GET maxmemory-policy
```

Anything other than `noeviction` or a `volatile-*` policy that respects TTLs
means raising the TTL will not help.

### 4. Check the client re-initializes

The spec is explicit: a client that receives 404 for a request carrying a
session id **MUST** start a new session by sending a fresh `InitializeRequest`.
A client that instead surfaces the 404 as a tool failure turns a recoverable
condition into an outage that needs a human. If yours does that, see below.

## Sessions keep breaking and you don't control the client

Turn them off:

```python title="settings.py"
SPEC_MCP = {"SESSIONS_ENABLED": False}
```

This is a **conformant mode, not a relaxation.** Both legacy revisions say a
server *"MAY assign a session ID at initialization time"*, and make the client's
duty to echo one back conditional on it having arrived. A server that never
assigns is never sent one.

With it off, the `initialize`-handshake era runs statelessly: no id is minted,
none is required, and a client still echoing a stale id is ignored rather than
rejected — so flipping the setting does not itself cause the outage it prevents.

**What you give up:** server-initiated messaging on the legacy era. The session
id is what addresses a client's SSE channel, so the `GET` stream has no address
and answers `405`; the session `DELETE` does likewise. Request/response tool
calling is untouched.

**What it does not affect:** the modern (`2026-07-28`) era, which is stateless
already and ignores this setting entirely. If your client speaks it, none of
this section applies to you.

## `ImproperlyConfigured` on startup

### "its kernel spec declares no permissions"

A tool or resource must declare permissions on its spec. The endpoints are
plain Django views, so a project's `REST_FRAMEWORK` default permission classes
**do not reach MCP**, and a spec with none would ship as an open tool. Declare
the checks on the spec, `[Unrestricted()]` for an operation open to every
caller:

```python
from django_service_specs import ServiceSpec, Unrestricted

ServiceSpec(service=create_invoice, permissions=[Unrestricted()])
```

A `scopes=` gate at registration does not count: it is a transport check beside
the spec's permissions. To migrate a large surface gradually, downgrade the
refusal to a warning:

```python
SPEC_MCP = {"REQUIRE_TOOL_PERMISSIONS": False}
```

!!! tip "If your tests assign the settings dict"

    `settings.SPEC_MCP = {...}` **replaces** the dict rather than
    merging, so a project-level opt-out disappears inside any test that does
    that. Add the key to those literals too.

### "outputSchema would be advertised but structuredContent is disabled"

The spec requires a tool declaring an `outputSchema` to return conforming
`structuredContent`. This is caught when the tool is registered rather than on
the first call.

Note that server-wide `INCLUDE_OUTPUT_SCHEMA=True` with
`INCLUDE_STRUCTURED_CONTENT=False` is **legal** — it just requires every binding
to override the content back on. The error names the binding that did not.

### "paginate and max_page_size belong to a LIST selector tool"

`register_tool` refuses a knob of the other kind of tool: `paginate` or
`max_page_size` on a service tool, whose result is never paginated, and
`invalidates` on a selector tool, which changes nothing. The spec's class says
which kind of tool it is; move the knob to the tool it belongs to.

### "are declared by two sources" { #takes-the-name-the-specs-list-travels-under }

A `ServiceSpec` with `many=True` takes its list under one argument, named by the
spec's `many_argument` (default `items`; see
[A list payload](concepts.md#list-payload)). A read declared under that name
would compete with the list for the one argument, so the kernel refuses the
spec when it is built. Rename the read, or give the list another name on the
spec:

```python
from django_service_specs import DataclassValidator, ServiceSpec

ServiceSpec(
    service=create_invoices,
    validator=DataclassValidator(InvoiceRow),
    many=True,
    many_argument="invoices",
    permissions=[Unrestricted()],
)
```

### "ServiceSpec declares a collection_selector_spec with many=True"

The kernel raises this when the spec is built, before any registration, and
the same for an `instance_selector_spec` beside `many=True`. A list of rows
resolves no target, so dispatch would never run the lookup. Declare no target
lookup, or drop `many=True`.

## Refused when registering a resource

### "a kernel resource reads one row"

`register_resource` takes a `RETRIEVE` `SelectorSpec`: a resource names one
row by its URI, and the URI template's variables are the spec's reads. A `LIST`
selector is refused with `ImproperlyConfigured`; register it as a selector tool,
which pages it.

### "permissions belong to the selector= form"

A kernel spec declares its own presenter and permissions, so `permissions=`,
`output_serializer=`, `mime_type=` and `encoding=` beside a spec passed
positionally are refused with a `TypeError`. Declare the checks on the spec,
and add a `scopes=` gate at registration if the resource needs one.

## OAuth discovery returns 404s

### The authorization server must be a site root

Endpoint paths are appended to it, so passing the value django-oauth-toolkit
advertises as *its* issuer (`https://host/oauth`) publishes
`https://host/oauth/oauth/authorize/` and two siblings like it. Pass the site
root. This warns at construction; if you genuinely mount elsewhere, set the
paths instead:

```python
DjangoOAuthToolkitBackend(
    authorization_servers=["https://host"],
    authorize_path="/custom/authorize/",
)
```

## A tool result is too large for the client

`MAX_RESULT_BYTES` (default 5 MiB) bounds the wire payload, but a client's
context window is far smaller — a result well under the ceiling can still be
undeliverable. The bound logs at `WARNING` when it fires.

If a paginated tool still returns too much, the row count is not the problem:
check how wide each row is. A presenter that expands related objects can
make ten rows larger than a thousand lean ones, and `limit` cannot express a
byte budget.

## A tool call answers `Internal error`

A `tools/call` answered `-32603` with the message `Internal error`, under HTTP
`500`, is an exception that escaped the dispatch: the service, the selector or
the presenter raised something no refusal arm answers. The exception is logged
at `ERROR` with its traceback; its text is not in the response, so read the
log. The response still carries the request's `id`, so a client matching
responses by `id` reports the error rather than a parse failure.

The common cause is a field selection written against the page envelope, for
example `fields=items` on a `paginate=True` tool. The presenter renders each
item, so a presenter narrowing each row to the named fields looks up a field no
item has, and raises. Select per item,
`fields=id,number`: see [Query params](concepts.md#query-param-per-item). A
presenter that refuses a shape the caller sent with a `ServiceValidationError`
is answered as an `isError` `validation_error` result, the same as any other
validation failure, so declaring the shape's `choices` on its
`Parameter` turns this `500` into a correctable answer.

A `PermissionDenied` raised inside the call is the exception: it is a refusal,
not a server fault, so it is answered `-32006` under `403` with the
`WWW-Authenticate` challenge, exactly like a permission check that answered
no, and nothing is logged at `ERROR`.

A progress-carrying call (one sent with a `progressToken`) has committed its
`200` before the dispatch runs, so the same failure arrives as its last frame,
a `-32603` whose message is `Internal error` too.
