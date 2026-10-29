# Authentication

`django-spec-mcp` is a **resource server**: it validates bearer tokens
that someone else issued. The library does not implement a token issuer (that's
the job of an Authorization Server / IDP). It does ship two backends and the
RFC 9728 metadata endpoint clients use to discover them.

## The pieces

| Surface | Protocol | Default |
| --- | --- | --- |
| `MCPAuthBackend` | Authenticate request → `TokenInfo`, build `WWW-Authenticate`, supply PRM payload | **always** `DjangoOAuthToolkitBackend` unless you pass `auth_backend=`; mounting refuses if its extra is missing |
| The spec's `permissions` | Kernel `PermissionCheck`s guarding a tool or resource | required: a spec with none is refused at registration |
| `scopes=` | OAuth scopes a binding's caller must carry | `None` (no scope gate) |
| `permissions=` on a prompt or view | Kernel `PermissionCheck`s, each handed the binding as the spec | `[]`; a prompt with neither these nor `scopes=` is refused |
| `/.well-known/oauth-protected-resource` | RFC 9728 metadata | served from backend's `protected_resource_metadata()` |

The transport flow on every request:

1. Validate `Origin`, `MCP-Protocol-Version`, `MCP-Session-Id` (where required).
2. `backend.authenticate(request)` → `TokenInfo | None`. `None`, the answer
   for a caller the backend cannot authenticate, → 401 with the challenge from
   `backend.www_authenticate_challenge(...)`. A backend that raises instead is
   not answered as MCP: a `PermissionDenied` or `Http404` renders as Django's
   own HTML 403 or 404, and anything else is a 500. From here on
   `request.user` and `request.auser()` are the token's principal.
3. The binding's scope gate and its spec's permissions are asked; a denial →
   403, with any required scopes surfaced in the challenge.
4. Handler dispatched.

## `AllowAnyBackend` (dev only)

Authenticates every request as anonymous. The metadata payload is intentionally
minimal and includes a `_warning`. Don't ship this to production.

Pass it when you build the server:

```python
from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend

server = MCPServer(name="dev", auth_backend=AllowAnyBackend())
```

## `DjangoOAuthToolkitBackend`

Wraps [django-oauth-toolkit](https://django-oauth-toolkit.readthedocs.io/) as a
resource server. Bearer tokens are validated against DOT's `AccessToken` model;
scopes are projected into the `TokenInfo`. The package import is **lazy** — the
backend module imports cleanly even without `oauth2_provider` installed, so a
server used purely in process (`call_tool`, `list_tools`) needs no DOT at all.

Mounting is different, because from there on every request reaches
`authenticate()`. `server.urls` and `server.async_urls` ask the backend to check
itself first, so a missing extra is an `ImproperlyConfigured` while the URLConf
is imported — which `manage.py check` reaches — rather than a 500 on the first
request. Backends opt into that by implementing `check_configuration()`; one
that needs no setup implements nothing and is left alone.

```bash
pip install "django-spec-mcp[oauth]"
```

```python
INSTALLED_APPS = [
    # ...
    "oauth2_provider",
]

# DjangoOAuthToolkitBackend is the default — a server with no auth_backend=
# gets one. Pass it explicitly only to configure it.
SPEC_MCP = {
    "SERVER_INFO": {
        "resource": "https://example.com/mcp/",
        "authorization_servers": ["https://example.com/oauth/"],
        "scopes_supported": ["invoices:read", "invoices:write"],
        "resource_metadata_url": "https://example.com/mcp/.well-known/oauth-protected-resource",
        "documentation": "https://example.com/docs/mcp/",
    },
}
```

The `SERVER_INFO` keys flow into both:

- `protected_resource_metadata()` — what the PRM endpoint returns.
- `www_authenticate_challenge()` — built from `resource_metadata_url`,
  any required scopes, and the `error="invalid_token"` code on auth failure.

!!! note "`name` and `version` belong on the server"
    `SERVER_INFO["name"]` / `["version"]` are the **fallback** for a server
    built without `name=` / `version=`. Prefer the constructor — it is the only
    way to give two servers in one project distinct identities:

    ```python
    MCPServer(name="internal", version="2.0.0", url_namespace="internal-mcp")
    ```

## Who may call a tool

!!! warning "`REST_FRAMEWORK` settings do not reach `/mcp/`"
    The endpoints and the protected-resource metadata are plain Django views,
    so nothing in a project's `REST_FRAMEWORK` setting applies to them: not its
    default permission or authentication classes, not its throttles, renderers
    or `EXCEPTION_HANDLER`. What gates an MCP call is the auth backend above,
    the spec's own `permissions`, the `scopes=` gate at registration, and the
    `rate_limits=` of each tool, resource and prompt
    ([Add rate limiting](recipes/rate-limiting.md)).

A tool or resource is guarded by its spec's `permissions`: a list of kernel
[`PermissionCheck`][django_service_specs.PermissionCheck] instances, the same
checks every other transport on the kernel asks. Each is asked through the
kernel's `dispatch` of the principal the backend resolved, `TokenInfo.user`,
before any rate limit is charged, the target is looked up or the service runs.

Registering a spec that declares no `permissions` at all is **refused**:
`ImproperlyConfigured` at registration. `[Unrestricted()]` is the honest choice
for an operation open to every caller, because it says so out loud. Set
`SPEC_MCP["REQUIRE_TOOL_PERMISSIONS"] = False` to downgrade the refusal to an
`UnguardedToolWarning`; a spec declaring `permissions=None` then runs each call
under a kernel `Grant` built for that spec and that caller, so it is called
with no permission check. A `scopes=` gate does not count as a guard either
way. See [Kernel specs](concepts.md#kernel-specs).

A check is a subclass with a `has_permission(principal, spec)` method. A Django
permission is a few lines:

```python
from django_service_specs import PermissionCheck, ServiceSpec


class HasDjangoPerm(PermissionCheck):
    def __init__(self, codename: str) -> None:
        self.codename = codename

    def has_permission(self, principal, spec) -> bool:
        return principal.has_perm(self.codename)


server.register_tool(
    ServiceSpec(service=refund_invoice, permissions=[HasDjangoPerm("invoices.refund_invoice")]),
    name="invoices.refund",
    description="Refund an invoice.",
    scopes=("invoices:write",),
)
```

A check refusing the caller is answered `-32006` "Insufficient permission"
with HTTP 403. The challenge names no `scope`, because no scope the client could
obtain would change the answer.

**A check that depends on the token** rather than the user, a claim say, asks
for the call's context by declaring a keyword `call_context`. Over HTTP its
`"request"` is the Django `HttpRequest`, carrying the backend's `TokenInfo` as
`request.mcp_token`; `request.user` and `request.auser()` are the token's
principal from authentication on, whatever the session cookie names. An
in-process `call_tool` or `acall_tool` is not over HTTP: its `"request"` may be
`None`, or a request whose `mcp_token`, when it has one, carries the `scopes=`
the call passed and `raw=None`. So a check reading the token decides for itself
what an in-process call means, and never tells one apart by `request is None`:

```python
from django_service_specs import PermissionCheck


class HasClaim(PermissionCheck):
    def __init__(self, claim: str) -> None:
        self.claim = claim

    def has_permission(self, principal, spec, *, call_context) -> bool:
        request = call_context.get("request")
        token = getattr(request, "mcp_token", None)
        return token is not None and self.claim in (token.raw or {})
```

A check may ask for the call's `arguments` the same way, and for a registered
pool seed by its name. A check is **synchronous**: one declaring an `async def`
method is refused with `ImproperlyConfigured` when the spec is built, because an
un-awaited coroutine is truthy and would allow every call.

### The scope gate

`scopes=` on `register_tool`, `register_resource`, `register_prompt` and
`register_ui_resource` names the OAuth scopes the caller's token must carry, every one of them. A bare string is one scope:
`scopes="invoices:write"`. A token missing one is answered `-32006` with HTTP
403, every declared scope in `data.requiredScopes`, and a challenge carrying
`error="insufficient_scope"` and the scopes, so the client knows what to ask
for. `scopes=()` is refused, since it would read as a gate and admit every
caller.

The scope gate is a transport check, beside the spec's permissions rather than
in place of them: on a tool or resource it never satisfies
`REQUIRE_TOOL_PERMISSIONS`, which reads the spec's own `permissions`. A prompt
has no spec, so there either guard counts.

### Prompts and interactive views

A prompt and an interactive view are not specs, so they are gated where they
are registered: `register_prompt` and `register_ui_resource` take
`permissions=[...]`, a list of the same kernel `PermissionCheck`s a spec
declares, and `scopes=`, the scope gate above. The scope gate is asked first,
then a deactivated caller is refused, then each check in order, the first
refusal ending it. A check is asked `has_permission(principal, binding)`:
`principal` is the token's user, an `AnonymousUser` when it has none, and
`binding` is the prompt's or view's binding standing where a spec would, so a
check can read its `name`.

```python
from django_service_specs import PermissionCheck


class IsStaff(PermissionCheck):
    message = "Staff only."

    def has_permission(self, principal, spec, **kwargs):
        return bool(principal.is_staff)


server.register_prompt(
    name="summarise-ledger",
    render=render_summary,
    permissions=[IsStaff()],
    scopes="ledger:read",
)
```

The same judgement answers `prompts/list`, `prompts/get`, `resources/list`,
`resources/read`, `completion/complete` and a subscription, so a caller is
never shown a prompt or view it would then be refused.

Registration refuses a list that cannot be judged: a bare string (it would
spread into one entry per character), a check class rather than an instance,
an object that is not a `PermissionCheck`, and a check whose `has_permission`
is `async def`, whose answer would be a truthy coroutine nothing awaits. A
check that answers one anyway at call time is refused there too. Only the
principal and the binding are passed: the keywords a spec's check may opt into,
such as `arguments` or `call_context`, are the kernel's to offer at dispatch,
so a check that requires one cannot gate a prompt.

A prompt with neither `permissions=` nor `scopes=` is refused like a tool. Interactive
**views** (`register_ui_resource`) are the one exemption, and a deliberate
one — a view is a template rendered with no context, a literal document, or a
zero-argument callable, none of which can read the caller's data.

That third source is caller-blind **because registration enforces it**: a
`selector=` declaring any fillable parameter is refused, since the read path
resolves every binding's callable against a pool carrying `request` and
`user`. Without that refusal the exemption would be unsound for one of its
three cases — a caller-aware view registered unguarded, served into a document
hosts may cache across callers. If you want a view whose content depends on
who is asking, you want `register_resource`, where the declaration check
applies.

### Object-level permissions

A check's `has_object_permission(principal, spec, target)` runs against the
row the dispatch resolved: the target of a retrieve, or the instance an update
acts on. It allows by default, because most checks are class-level only.
`has_scope_permission(principal, spec, scope)` is the same question for a call
that resolves no single row: the set a bulk call acts on or a collection
reads, and `None` for a create. A kernel spec's checks are asked once on a
plain call, and on a streamed call once in its pre-flight and once more when
the call runs, when its arguments pass the kernel's argument check.

A resource read runs the same `dispatch`, so its spec's checks see the row the
URI names. `completion/complete` on a resource template names the template
rather than a URI, so the spec's checks are asked on no arguments, and a check
scoping by a URI variable refuses the completion, whether it answers no on the
missing arguments or raises on them.

### Filtering listings by permissions

By default `tools/list`, `resources/list`, `resources/templates/list`,
and `prompts/list` return every registered binding regardless of whether
the current caller's permissions would let it invoke one. (A tool that an
operation-scope affordance refuses right now is the exception: `tools/list`
leaves it out either way, because that condition cannot depend on the
call's arguments — see
[Concepts](concepts.md#a-tool-that-cannot-run-now-is-not-listed).) Set
`SPEC_MCP["FILTER_LISTINGS_BY_PERMISSIONS"] = True` to drop
bindings whose permissions deny the caller before paginating.

This is **binding-level** gating. A binding's `scopes=` gate is evaluated
against the caller's token, and a prompt's or view's checks against its
principal, as a `prompts/get` or `resources/read` asks them. A tool or
resource is judged by its spec's own checks as well, asked through the
kernel's `authorize` with no arguments, because a listing has none; a create,
a service spec with no instance or collection selector, is also asked each
check's `has_scope_permission` of `None`, as every call to it is. Both
questions get the caller's principal, the server's pool seeds and a
`call_context` whose request carries the token, as a call does. A caller the
checks refuse is not shown the binding, and neither is a deactivated caller. A
spec declaring `permissions=None` is listed only where its call is granted, as
`register_tool` grants it with `REQUIRE_TOOL_PERMISSIONS` off.

A check whose decision depends on the call arguments cannot answer at list time
and will conservatively deny: when asking it raises, that binding alone is
hidden and the rest of the listing is unaffected. Mark such a binding with
`always_listed=True` to keep it visible as a discovery aid; the check still
gates the actual invocation. An `ImproperlyConfigured` raised by a check is not
a refusal: it is raised and fails the listing, because the call raises it too.

## Audience binding (RFC 8707)

`resource_url` is the identity this server **publishes** — RFC 9728 requires it
in protected-resource metadata, and it is what the `WWW-Authenticate` challenge
points clients at. Setting it does **not**, on its own, reject anything.

!!! warning "Enforcement is a separate, opt-in knob — and you probably want it on"
    Audience enforcement needs the access token to record which resource it was
    issued for.

    **django-oauth-toolkit 3.4.0 (2026-07-23) added RFC 8707 resource
    indicators**: stock `AccessToken` carries a `resource` field and an
    `allows_audience()` check, and `resource` is accepted at both the authorize
    and token endpoints. On 3.4.0 or later, `ENFORCE_AUDIENCE = True` needs
    nothing else.

    It is nonetheless **off by default**, and since the `[oauth]` extra floors
    DOT at `>=3.4` that is not about the floor. A token that records *no*
    resource is rejected, and one only records a resource if the client sent
    RFC 8707's `resource` parameter. The MCP spec obliges MCP clients to send
    it; the other clients of your authorization server — a browser app, a
    script, a mobile client — are under no such obligation, so a default of
    `True` would 401 them the day you upgrade this package. Choosing
    enforcement is a statement about your client population, which is yours to
    make. The check itself is capability-based rather than version-based, so it
    asks your configured token model rather than DOT's version number.


!!! danger "The MCP spec makes this a MUST, so treat the default as a floor, not a recommendation"
    `2026-07-28` says a resource server **MUST** validate that access tokens were
    issued specifically for it. Leaving `ENFORCE_AUDIENCE` off on DOT 3.4.0+ is a
    conformance gap, and the failure mode is cross-resource token replay: a token
    minted for a different resource on the same authorization server is accepted
    here.

    Turn it on. The `[oauth]` extra floors DOT at `>=3.4`, so the only
    deployments that cannot are those running a swapped `ACCESS_TOKEN_MODEL`
    without the field, or a DOT installed outside this extra and pinned older —
    and both of those have an `audience_getter=` available. Before flipping it,
    check that the clients you serve send `resource` on the authorize and token
    requests, because a token that carries none is rejected.

To enforce, tell the backend where the audience actually lives — either a
swapped `OAUTH2_PROVIDER["ACCESS_TOKEN_MODEL"]` carrying a `resource` field, or
an explicit `audience_getter`:

```python
MCPServer(
    name="internal-mcp",
    auth_backend=DjangoOAuthToolkitBackend(
        resource_url="https://example.com/internal/mcp/",
        enforce_audience=True,
        # Wherever the resource really arrives: a JWT claim, a gateway header,
        # a related row. Returning None rejects the token.
        audience_getter=lambda token: token.jwt_claims.get("aud"),
    ),
)
```

Turning enforcement on without one of those raises `ImproperlyConfigured` at
startup, naming both ways out — a server that rejects everything is a
configuration error, and the only useful place to say so is where the
configuration is read, not in a 401 per request.

Because enforcement needs a getter, it means bringing your own backend:
`MCPServer(resource_url=...)` configures the default backend for *metadata*.

```python
SPEC_MCP = {
    "RESOURCE_URL": "https://example.com/mcp/",
    "SERVER_INFO": {
        "authorization_servers": ["https://example.com/oauth/"],
        "scopes_supported": ["invoices:read", "invoices:write"],
        "resource_metadata_url": "https://example.com/mcp/.well-known/oauth-protected-resource",
    },
}
```

`RESOURCE_URL` is the **default** for a server that doesn't name its own. Since
RFC 8707 binds a token to *a* resource, each server needs its own canonical URL
— that binding is exactly what stops a token issued for one resource being
replayed against another, and two servers sharing one URL defeat it:

```python
# urls.py
internal = MCPServer(
    name="internal-mcp",
    resource_url="https://example.com/internal/mcp/",
    url_namespace="internal-mcp",
)
public = MCPServer(
    name="public-mcp",
    resource_url="https://example.com/public/mcp/",
    url_namespace="public-mcp",
)

urlpatterns = [
    path("internal/mcp/", internal.urls),
    path("public/mcp/", public.urls),
]
```

A token minted for `public-mcp` is now rejected by `internal-mcp`. Each server's
`WWW-Authenticate` challenge also points at **its own** PRM endpoint, derived
from its `resource_url` — so discovery lands on the right metadata.

`resource_url=` configures the default backend. If you bring your own
`auth_backend=`, it owns its audience binding — configure it there
(`DjangoOAuthToolkitBackend(resource_url=...)`); passing both raises.

`RESOURCE_URL` is also what the PRM endpoint advertises as `resource`, so one
value drives both "what we tell clients to ask for" and — when `ENFORCE_AUDIENCE`
is on — "what we accept". Leaving it unset publishes an empty `resource`, which
RFC 9728 marks REQUIRED, so the metadata carries a `_warning` saying why.

!!! note "Why exact-match"
    Token audiences are URLs, not patterns. Substring matches and prefix
    matches are unsafe (a token bound to `…/mcp` would otherwise satisfy a
    server expecting `…/mcp-admin`). The implementation enforces equality only.

### If you are writing a resource server against the official `mcp` SDK

!!! danger "The SDK publishes the metadata for you and never validates the audience"
    This is the single easiest thing to get wrong in this area, and it is not
    specific to this package — it is worth knowing wherever you build an MCP
    resource server in Python.

    `mcp.shared.auth_utils` provides `resource_url_from_server_url()` and
    `check_resource_allowed()`, and in the shipped wheel they are called **only
    from client paths**. `AccessToken` carries a `resource` field, but
    `BearerAuthBackend.authenticate()` checks the bearer prefix, the verifier's
    verdict and `expires_at` — and nothing else.

    So a `TokenVerifier` that merely checks a signature is **non-compliant with
    the audience MUST, and cross-resource token replay works**: a token minted
    for another resource on the same authorization server is accepted. FastMCP's
    `JWTVerifier` does validate audience out of the box; the official SDK's
    verifiers do not.

    If you are using this package's `DjangoOAuthToolkitBackend`, that is what
    `ENFORCE_AUDIENCE` is for and the check runs server-side. If you are writing
    your own verifier anywhere, validate the audience in it.

## The documented deployment: django-oauth-toolkit with CIMD

This is the configuration this project recommends and tests against. Everything
above is a seam you can replace; this section is the answer if you do not want
to make those choices yourself.

DOT acts as the Authorization Server as well as backing the resource server. The
MCP package only consumes the tokens it issues — the authorization endpoint,
token endpoint, refresh flow and client registration are all DOT's. Modern MCP
clients (Claude Desktop, Inspector) discover them through PRM → AS metadata.

Two settings carry most of the weight, and they are the reason this deployment
is the recommended one rather than one option among several:

- `ENFORCE_AUDIENCE` makes the RFC 8707 audience check mandatory, which the MCP
  specification requires of a resource server. DOT 3.4.0 and later record the
  `resource` on the token, so this works with the stock model.
- `CIMD_ENABLED` turns on Client ID Metadata Documents, which is how recent MCP
  clients register. It replaces Dynamic Client Registration, which the
  specification deprecates. See [Client ID Metadata
  Documents](#client-id-metadata-documents) below for what it does and what to
  decide about it.

Both arrived in django-oauth-toolkit **3.4.0**, and that is where the `[oauth]`
extra floors it — a recommendation the package's own extra could not install
would not be one. Below 3.4 there is no `CIMD_ENABLED` key for DOT to read and
no `resource` on the token for the audience check to compare, and neither
failure announces itself: the server advertises no CIMD support and every
client quietly falls back to the deprecated path.

```python title="settings.py"
INSTALLED_APPS = [
    # ...
    "oauth2_provider",
]

OAUTH2_PROVIDER = {
    # DOT 3.4.0+ accepts an RFC 8707 `resource` at the authorize and token
    # endpoints and records it on the token, so audience binding needs nothing
    # here — turn on ENFORCE_AUDIENCE instead. See "Audience binding" above.
    "SCOPES": {
        "invoices:read": "Read invoices",
        "invoices:write": "Mutate invoices",
    },
    # Token lifetimes appropriate for an MCP session — short access tokens,
    # refresh on demand.
    "ACCESS_TOKEN_EXPIRE_SECONDS": 600,
    "REFRESH_TOKEN_EXPIRE_SECONDS": 60 * 60 * 24,
    # Client ID Metadata Documents: how recent MCP clients register, and the
    # replacement for the deprecated Dynamic Client Registration. DOT publishes
    # `client_id_metadata_document_supported` in its AS metadata from this
    # setting, and this package reads the same setting when it builds its own
    # AS metadata, so the two cannot disagree.
    "CIMD_ENABLED": True,
    # Registration over CIMD happens before anyone has authenticated, so DOT's
    # default permission is open: any URL that serves a valid document becomes
    # a client. That is the CIMD model working as intended and it is the right
    # default for a public server. Narrow it if this server's tools are not
    # meant for arbitrary clients — see "Deciding how open CIMD should be".
    "CIMD_REGISTRATION_PERMISSION_CLASSES": ("oauth2_provider.cimd.AllowAllCIMDPermission",),
}

SPEC_MCP = {
    "RESOURCE_URL": "https://example.com/mcp/",
    "ALLOWED_ORIGINS": ["https://app.example.com"],
    # The specification requires a resource server to validate that a token was
    # issued for it. Off by default because a token recording no resource is
    # rejected, and only a client that sends RFC 8707's `resource` parameter
    # records one -- which MCP clients must and other OAuth clients need not.
    # For a server whose clients are MCP clients, this is the correct setting.
    "ENFORCE_AUDIENCE": True,
    "SERVER_INFO": {
        "authorization_servers": ["https://example.com/oauth/"],
        "scopes_supported": ["invoices:read", "invoices:write"],
        "resource_metadata_url": "https://example.com/mcp/.well-known/oauth-protected-resource",
    },
}
```

!!! warning "Install the extra"
    `DjangoOAuthToolkitBackend` is the default backend, and mounting refuses
    without DOT: `pip install "django-spec-mcp[oauth]"`. The
    refusal happens while the URLConf is imported, so `manage.py check` catches
    it before a request ever arrives. The extra pulls
    `django-oauth-toolkit>=3.4`, so the two settings above are always available;
    if you declare DOT yourself instead, declare it at 3.4 or later too.

```python title="urls.py"
from django.urls import include, path

from invoices.mcp import server

urlpatterns = [
    path("oauth/", include("oauth2_provider.urls", namespace="oauth2_provider")),
    path("mcp/", server.urls),
]
```

Verify the AS publishes [RFC 8414](https://datatracker.ietf.org/doc/html/rfc8414)
metadata before debugging client-side issues — DOT supports this, but the URL
is configurable. From a shell:

```bash
curl https://example.com/oauth/.well-known/oauth-authorization-server | jq .
```

You should see at minimum `issuer`, `authorization_endpoint`, `token_endpoint`,
and `client_id_metadata_document_supported: true`. Check `none` is in
`token_endpoint_auth_methods_supported` while you are there: some hosts, Claude
among them, require *both* before they will pick CIMD, and drop to DCR if
either is missing.

A `registration_endpoint` appears too, and appears whenever an issuer is
configured — including where DCR is switched off. That is not a leak: the
mechanism a client picks is its own choice under the specification's priority
order, and a client that reads `client_id_metadata_document_supported` never
looks at `registration_endpoint`. Withdrawing it would only ever be read by
clients that have no other way in.

The PRM endpoint you serve points clients at this AS, so a missing or wrong URL
here is the most common cause of "Inspector can't authenticate" reports.

### What the round-trip looks like

1. Client hits `tools/call` without a token.
2. Server returns 401 with
   `WWW-Authenticate: Bearer resource_metadata="https://example.com/mcp/.well-known/oauth-protected-resource", error="invalid_token"`.
3. Client fetches that URL → reads `authorization_servers`.
4. Client fetches `<as>/.well-known/oauth-authorization-server` → reads
   `client_id_metadata_document_supported` (CIMD), falling back to
   `registration_endpoint` (the deprecated DCR) if it does not speak CIMD.
5. Client publishes a Metadata Document at its own `client_id` URL — or
   pre-registers — then walks the authorization-code flow with
   `resource=https://example.com/mcp/`, so the issued access token is
   audience-bound to this server.
6. Client retries `tools/call` with the bearer token; server validates the
   token, checks audience, dispatches.

## Client ID Metadata Documents

Recent MCP clients prefer **Client ID Metadata Documents**
([draft-ietf-oauth-client-id-metadata-document](https://datatracker.ietf.org/doc/draft-ietf-oauth-client-id-metadata-document/))
over Dynamic Client Registration, which the MCP specification deprecates. A
client uses an **HTTPS URL as its `client_id`** and publishes a JSON metadata
document there; the authorization server fetches it. There is no registration
round-trip and no registration state to keep, and a client can rotate without
re-registering.

The deprecation has a date attached: the specification's deprecated-features
registry gives DCR an earliest removal of *the first revision released on or
after 2027-07-28*. Until then both mechanisms are legal to serve, and serving
both is usually the right call rather than a hedge: a client that cannot speak
CIMD often has no path in but DCR.

**DOT implements this natively from 3.4.0.** Set `CIMD_ENABLED = True` and DOT
advertises it in AS metadata, resolves URL-shaped client IDs, fetches and
validates the document, and provisions the `Application` row:

```json
{
  "issuer": "https://example.com/oauth/",
  "authorization_endpoint": "https://example.com/oauth/authorize/",
  "token_endpoint": "https://example.com/oauth/token/",
  "client_id_metadata_document_supported": true
}
```

This package reads the same `CIMD_ENABLED` setting when it builds its own AS
metadata, rather than carrying a flag of its own, so the two can never disagree
about what the deployment supports.

!!! danger "Do not hand-roll the fetch"
    Earlier versions of this page suggested fronting DOT with a small view that
    accepted a URL-shaped `client_id` and fetched it. That advice was wrong once
    DOT 3.4.0 shipped, and it was the dangerous kind of wrong: *fetch a URL the
    client just supplied* is a server-side request forgery unless it is
    carefully defended, and the defence is the hard part. DOT's fetcher
    validates the URL, resolves the hostname and rejects private and loopback
    addresses (so a DNS rebind does not slip through), caps the document size,
    the timeout, the number of concurrent fetches and the retry rate after a
    failure, and honours cache lifetimes. Use it.

### Deciding how open CIMD should be

CIMD registration happens on the pre-authorization path, where nobody has
authenticated yet, so DOT's default permission class is open: any URL serving a
valid document can become a client. That is the CIMD model working as designed —
the URL *is* the identity, and it is fetched rather than asserted — and it is
the right default for a server whose tools are meant for any client the user
authorizes.

It is the wrong default for a server whose tools are not. Narrow it with the
host allowlist:

```python title="settings.py"
OAUTH2_PROVIDER = {
    "CIMD_ENABLED": True,
    "CIMD_REGISTRATION_PERMISSION_CLASSES": ("oauth2_provider.cimd.HostAllowlistCIMDPermission",),
    # Django ALLOWED_HOSTS syntax: an exact host, or ".example.com" for a
    # domain and its subdomains. An empty list denies everything.
    "CIMD_ALLOWED_HOSTS": [".anthropic.com", "inspector.example.com"],
}
```

The remaining knobs — `CIMD_FETCH_TIMEOUT_SECONDS`, `CIMD_MAX_DOCUMENT_SIZE`,
`CIMD_METADATA_MIN_AGE_SECONDS` / `_MAX_AGE_SECONDS`,
`CIMD_FAILURE_BACKOFF_SECONDS`, `CIMD_MAX_CONCURRENT_FETCHES` — have sensible
defaults and are documented by DOT. `manage.py clearcimdapplications` prunes
rows for documents that have stopped resolving.

### What the resource server sees

Nothing. A CIMD-registered client's access token is byte-identical to a
pre-registered client's, so `authenticate()`, the audience check and the
permission classes all behave the same way. CIMD is an authorization-server
mechanism; it is worth turning on because it is what clients expect, not
because this package does anything with it beyond advertising it.

## Try it with mcp-inspector

```bash
npx @modelcontextprotocol/inspector --url http://localhost:8000/mcp/
```

Inspector reads PRM, hits your AS metadata, walks the auth flow, and exercises
`tools/list` + `tools/call`. Common failure modes and where to look:

| Symptom | Likely cause |
| --- | --- |
| 401 with no `WWW-Authenticate` | Custom auth backend forgot to return a challenge. Check `www_authenticate_challenge`. |
| 401 with `WWW-Authenticate` but no `resource_metadata` | `SERVER_INFO["resource_metadata_url"]` not set. |
| Token accepted but every call still 401 | `ENFORCE_AUDIENCE` is on and the `audience_getter` returns something other than `RESOURCE_URL` (with stock DOT it returns `None`, since DOT records no resource). |
| 403 with `error="insufficient_scope"` and `scope=` in the challenge | Token authenticated, missing one of the per-binding scopes. The body also carries JSON-RPC `-32006` with `data.requiredScopes`. |
| 403 with no `scope=` | A spec's permission check refused the caller, or a prompt's or view's own check. RFC 6750 defines no error code for that case, so the challenge advertises nothing rather than a scope the client cannot obtain. |
| 403 with `INVALID_REQUEST` (`-32600`) in the body | Not a permission failure — `Origin` rejection. |
