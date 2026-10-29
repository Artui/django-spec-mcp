# Authentication

`django-spec-mcp` is a **resource server**: it validates bearer tokens
that someone else issued. The library does not implement a token issuer (that's
the job of an Authorization Server / IDP), and it does not verify a token for
you: how a token is verified depends on the authorization server a project
runs, so that backend is the project's to write. What ships is the seam: the
`MCPAuthBackend` protocol, the `bearer_challenge` helper that builds the 401
challenge, the exact audience comparison, the RFC 9728 metadata view clients use
to discover your authorization server, and `AllowAnyBackend` for development.

## The pieces

| Surface | Protocol | Default |
| --- | --- | --- |
| `MCPAuthBackend` | Authenticate request → `TokenInfo`, build `WWW-Authenticate`, supply PRM payload | **none**: `auth_backend=` is required to mount, and `urls` / `async_urls` refuse without one |
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

## Writing your own backend

A mounted server has no default backend. Every request to its endpoint reaches
`authenticate()`, and a package default would either verify against an
authorization server you may not run or answer every caller, so
`server.urls` and `server.async_urls` raise `ImproperlyConfigured` when
`auth_backend=` was not given. A server used purely in process (`call_tool`,
`list_tools`) never authenticates and needs no backend.

A backend implements `MCPAuthBackend`:

| Method | Answers |
| --- | --- |
| `authenticate(request)` | A `TokenInfo` for a request carrying a valid token, `None` for one that does not (the transport answers 401) |
| `protected_resource_metadata()` | The RFC 9728 document served at `.well-known/oauth-protected-resource`: the resource's URL and the authorization servers that issue tokens for it |
| `authorization_server_metadata()` | The metadata of an authorization server the backend hosts; a backend that hosts none raises `NotImplementedError` |
| `www_authenticate_challenge(scopes=, error=)` | The `WWW-Authenticate` value of a 401 or an insufficient-scope 403 |

This one verifies tokens with a function the project supplies (a JWKS check, an
introspection call, a database lookup) and binds each token to this server's
URL:

```python
from django.http import HttpRequest

from django_spec_mcp.auth import TokenInfo, bearer_challenge
from django_spec_mcp.auth.audience import audience_matches
from django_spec_mcp.auth.types.authorization_server_metadata import (
    AuthorizationServerMetadata,
)
from django_spec_mcp.auth.types.protected_resource_metadata import ProtectedResourceMetadata


class BearerBackend:
    """Verifies tokens an outside authorization server issued for this server."""

    def __init__(self, *, resource_url, authorization_server, scopes_supported, verify):
        self._resource_url = resource_url
        self._authorization_server = authorization_server
        self._scopes_supported = scopes_supported
        self._verify = verify  # raw token -> TokenInfo, or None when it does not verify

    def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        scheme, _, raw = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not raw:
            return None
        info = self._verify(raw)
        # The audience MUST: a token issued for another resource on the same
        # authorization server verifies fine and must still be refused here.
        if info is None or not audience_matches(info.audience, self._resource_url):
            return None
        return info

    def protected_resource_metadata(self) -> ProtectedResourceMetadata:
        return ProtectedResourceMetadata(
            resource=self._resource_url,
            authorization_servers=[self._authorization_server],
            scopes_supported=list(self._scopes_supported),
        )

    def authorization_server_metadata(self) -> AuthorizationServerMetadata:
        raise NotImplementedError("The authorization server is not hosted here.")

    def www_authenticate_challenge(self, *, scopes=None, error=None) -> str:
        # The server mounts its metadata view under its own URL, so the pointer
        # a client follows to find the authorization server is derived from it.
        return bearer_challenge(
            resource_metadata_url=(
                f"{self._resource_url.rstrip('/')}/.well-known/oauth-protected-resource"
            ),
            scopes=scopes,
            error=error,
        )
```

```python
from django_spec_mcp import MCPServer

server = MCPServer(
    name="internal-mcp",
    auth_backend=BearerBackend(
        resource_url="https://example.com/internal/mcp/",
        authorization_server="https://idp.example.com",
        scopes_supported=["invoices:read", "invoices:write"],
        verify=verify_access_token,
    ),
)
```

`bearer_challenge(resource_metadata_url=, scopes=, error=)` is where the header
is formatted, once: the realm, then `resource_metadata`, `error` and `scope` in
the order a client reads them. `resource_metadata_url` has no default on
purpose. It is the URL a client follows to find your authorization server, and a
challenge without it leaves the client no way to start the flow, with no error
on either side. Pass the URL the server's `protected-resource-metadata` route
answers at (`https://example.com/internal/mcp/.well-known/oauth-protected-resource`
for a server whose resource URL is `https://example.com/internal/mcp/`), or
`None` when the backend publishes no metadata.

Mounting is where a backend becomes load-bearing, so a backend may check itself
there: one that implements `check_configuration()` (the `SelfChecking`
protocol) is asked once when `server.urls` or `server.async_urls` is read, and
raising `ImproperlyConfigured` from it surfaces while the URLconf is imported,
which `manage.py check` reaches, rather than as a 500 on the first request. A
backend that needs no setup implements nothing and is left alone.

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

    def has_permission(self, principal, spec) -> bool:
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

A spec's check whose decision depends on the call arguments cannot answer at list time
and will conservatively deny: when asking it raises, that binding alone is
hidden and the rest of the listing is unaffected. Mark such a binding with
`always_listed=True` to keep it visible as a discovery aid; the check still
gates the actual invocation. An `ImproperlyConfigured` raised by a check is not
a refusal: it is raised and fails the listing, because the call raises it too.

## Audience binding (RFC 8707)

The audience check is **the backend's job**, because only the backend knows where
the audience lives (a JWT `aud` claim, an introspection response, a column on the
token row). `TokenInfo.audience` carries it out, and `audience_matches` is the
comparison, as the worked backend above calls it:

- `audience_matches(token_audience, expected)` is exact equality. Token audiences
  are URLs, not patterns, and a prefix or substring match is unsafe: a token
  bound to `…/mcp` would otherwise satisfy a server expecting `…/mcp-admin`.
- A token that carries **no** audience is rejected when an `expected` URL is
  given, which is the point of configuring one.
- `expected=None` disables the check, for a deployment whose gateway already
  binds the token upstream.

!!! danger "The MCP spec makes this a MUST"
    `2026-07-28` says a resource server **MUST** validate that access tokens were
    issued specifically for it. A backend that only verifies a signature leaves a
    conformance gap, and the failure mode is cross-resource token replay: a token
    minted for a different resource on the same authorization server is accepted
    here. Before you require it, check that the clients you serve send RFC 8707's
    `resource` parameter on the authorize and token requests, because a token that
    carries none is rejected.

Each server needs its **own** canonical URL. RFC 8707 binds a token to *a*
resource, and that binding is what stops a token issued for one resource being
replayed against another; two servers sharing one URL defeat it:

```python
# urls.py
common = {
    "authorization_server": "https://idp.example.com",
    "scopes_supported": ["invoices:read"],
    "verify": verify_access_token,
}
internal = MCPServer(
    name="internal-mcp",
    auth_backend=BearerBackend(resource_url="https://example.com/internal/mcp/", **common),
    url_namespace="internal-mcp",
)
public = MCPServer(
    name="public-mcp",
    auth_backend=BearerBackend(resource_url="https://example.com/public/mcp/", **common),
    url_namespace="public-mcp",
)

urlpatterns = [
    path("internal/mcp/", internal.urls),
    path("public/mcp/", public.urls),
]
```

A token minted for `public-mcp` is now rejected by `internal-mcp`, and each
server's `WWW-Authenticate` challenge points at **its own** metadata endpoint.

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
    verifiers do not. If you are writing your own verifier anywhere, validate the
    audience in it.

## Try it with mcp-inspector

```bash
npx @modelcontextprotocol/inspector --url http://localhost:8000/mcp/
```

Inspector reads the protected-resource metadata, hits your authorization server's metadata, walks the auth flow, and exercises
`tools/list` + `tools/call`. Common failure modes and where to look:

| Symptom | Likely cause |
| --- | --- |
| 401 with no `WWW-Authenticate` | The backend's `www_authenticate_challenge` returned an empty value. Return `bearer_challenge(...)`. |
| 401 with `WWW-Authenticate` but no `resource_metadata` | The backend passed `resource_metadata_url=None` to `bearer_challenge`. |
| Token verifies but every call is still 401 | The backend's audience comparison fails: the token's audience is not the server's own resource URL (or the token carries none). |
| 403 with `error="insufficient_scope"` and `scope=` in the challenge | Token authenticated, missing one of the per-binding scopes. The body also carries JSON-RPC `-32006` with `data.requiredScopes`. |
| 403 with no `scope=` | A spec's permission check refused the caller, or a prompt's or view's own check. RFC 6750 defines no error code for that case, so the challenge advertises nothing rather than a scope the client cannot obtain. |
| 403 with `INVALID_REQUEST` (`-32600`) in the body | Not a permission failure — `Origin` rejection. |
