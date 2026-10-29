# Moving from drf-mcp

`django-spec-mcp` is `djangorestframework-mcp-server` (drf-mcp) carried onto the
[`django-service-specs`](https://github.com/Artui/django-service-specs) kernel.
It serves the same protocol from the same kind of mount, but the unit of
registration is a kernel spec, nothing in it imports Django REST framework, and
the names it stores outside the process are new. This page lists what a project
moving across has to do, in the order it meets each step. The changelog's
`[Unreleased]` section is the full list of changes; this page covers what you
have to act on.

## Before the first deploy: live state is dropped

Every name the server stores outside the process is new. The cache and Redis
prefixes are `spec-mcp:*` (`spec-mcp:session:`, `spec-mcp:task:`,
`spec-mcp:sse`, `spec-mcp:sse-replay`, `spec-mcp:sub` and the rate-limit
prefixes `spec-mcp:rl`, `spec-mcp:rl-sw` and `spec-mcp:rl-tb`), and an
elicitation's `requestState` is signed with a new salt,
`django_spec_mcp.elicitation.request_state`. Nothing reads drf-mcp's names, so
**the first deploy drops every live session, in-flight task, rate-limit window
and pending elicitation**:

- a client on the `2025-11-25` era is answered `404` on its session and starts
  a new one with `initialize`;
- a task created before the deploy is unknown to `tasks/get`;
- a rate-limit window starts again from zero;
- a `requestState` signed before the deploy fails its signature, so the
  answers it carried are dropped and the call asks again for what it still
  needs.

Deploy at a time when that is acceptable. A sessionless `2026-07-28` client
holds no session to lose.

## Install and imports

| drf-mcp | django-spec-mcp |
| --- | --- |
| `pip install djangorestframework-mcp-server` | `pip install django-spec-mcp` |
| `import rest_framework_mcp` | `import django_spec_mcp`, with the same module paths below it apart from the exceptions in this table and the modules removed below |
| `transport.streamable_http_viewset`, `transport.async_streamable_http_viewset`, `auth.protected_resource_metadata` | `transport.streamable_http_view`, `transport.async_streamable_http_view`, `auth.protected_resource_metadata_view`; see [What an HTTP client sees](#what-an-http-client-sees) |
| `rest_framework_mcp.constants` | `django_spec_mcp.types`, one module per name (`JsonRpcErrorCode` is in `django_spec_mcp.types.json_rpc_error_code`) |
| `UnknownArguments`, `RESERVED_POOL_SEEDS` from the package | import both from `django_service_specs`, which defines them |
| `service_spec_to_tool`, `selector_to_resource` from `django_spec_mcp.adapters`; `build_input_schema`, `build_output_schema` from `django_spec_mcp.schema` | gone: a kernel tool's schemas are the kernel's. `django_spec_mcp.schema.kernel_tool_input_schema` and `django_spec_mcp.schema.kernel_tool_output_schema` build what a tool advertises |
| the `jwt`, `spectacular`, `filter`, `oauth` and `toon` extras | gone. `filter_set=` on a selector spec needs `django-filter`, which the kernel's `filter` extra installs: `pip install "django-service-specs[filter]"` |

The root still exports what it exported from `constants`, apart from four names
that are gone: `UnknownArguments` (import it from the kernel), and
`ArgumentBinding`, `OutputFormat` and `ToolKind`, which the sections below
cover.
`django-service-specs` **0.20.0** is the lowest kernel release the package
runs on. It reserves `shapes` as a pool name, so a `PoolSeeds` entry named
`shapes` is refused with a `ValueError` when it is built, as one named `user` is.

djangorestframework, djangorestframework-services and
djangorestframework-dataclasses are no longer dependencies, and nothing in the
package imports them. If `"rest_framework"` is in `INSTALLED_APPS` only for
drf-mcp, remove it.

## Settings

Rename the settings key: everything drf-mcp read from `REST_FRAMEWORK_MCP` is
read from `SPEC_MCP`. **A `REST_FRAMEWORK_MCP` setting is no longer read, and
nothing warns about it**, so a project that only installs the new package runs
on the defaults until the key is renamed.

**No `REST_FRAMEWORK` setting reaches `/mcp/`.** The MCP endpoints and the
protected-resource metadata view are plain Django views, not DRF views, so a
project's default authentication and permission classes, its throttles
(`DEFAULT_THROTTLE_CLASSES` and the rest), its renderers and its
`EXCEPTION_HANDLER` do not apply to them. What authenticates a caller is the
server's `auth_backend=`; what guards a tool or resource is its spec's
`permissions` and the `scopes=` gate at registration; what limits a caller is
the `rate_limits=` on each tool, resource and prompt
([Add rate limiting](recipes/rate-limiting.md)).

These keys are read by nothing now, and nothing warns about them. Delete them:

| Key | Why it went |
| --- | --- |
| `DEFAULT_OUTPUT_FORMAT` | TOON is gone; a tool result is always JSON. See [Tool output](#tool-output-is-json). |
| `RESOURCE_URL`, `ENFORCE_AUDIENCE` | Only the django-oauth-toolkit backend read them, and it is gone. See [Authentication](#authentication). |
| `DCR_ENABLED`, `DCR_INITIAL_ACCESS_TOKEN`, `SIMPLEJWT_ACCESS_COOKIE` | Only the bundled authorization-server pieces read them, and they are gone. |
| `SERVER_INFO`'s `resource`, `authorization_servers`, `scopes_supported`, `resource_metadata_url` and `documentation` | The removed backend read them to build its metadata. A backend of your own publishes its metadata itself. |

The rest of `SERVER_INFO` is read as before, and its `name` defaults to
`django-spec-mcp`.

## Authentication

**A mounted server needs an `auth_backend=`.** drf-mcp fell back to its
django-oauth-toolkit backend; no backend that verifies tokens ships now, so
`server.urls` and `server.async_urls` raise `ImproperlyConfigured` naming the
server when none was given. `MCPServer(...)` itself still builds without one,
and `call_tool`, `acall_tool` and `list_tools` never authenticate, so a server
used only in process needs none.

What went with the django-oauth-toolkit backend:

- `DjangoOAuthToolkitBackend`, `UnenforcedAudienceWarning` and
  `MountedAuthorizationServerWarning`;
- `MCPServer(resource_url=)`, and the `RESOURCE_URL` and `ENFORCE_AUDIENCE`
  settings;
- the `oauth` extra;
- the `SERVER_INFO` keys the backend read, listed above;
- the bundled authorization-server pieces in `rest_framework_mcp.contrib`:
  Dynamic Client Registration, the authorization-server metadata and OpenID
  discovery views, `build_oauth_urlpatterns` and its `mcp-oauth-*` URL names,
  `check_oauth_url_shadowing`, `build_authorize_passthrough_view`,
  `AuthUserAdapter` and `SimpleJWTCookieAdapter`.

The authorization server is yours to run, and this package is the resource
server beside it. Write the backend for the authorization server you run:
[Writing your own backend](auth.md#writing-your-own-backend) walks through one
that verifies a token, checks its audience with `audience_matches` and builds its
`401` challenge with `bearer_challenge`:

```python
from django_spec_mcp.auth import bearer_challenge

bearer_challenge(
    resource_metadata_url="https://example.com/mcp/.well-known/oauth-protected-resource",
    scopes=["invoices:read"],
    error="insufficient_scope",
)
# 'Bearer realm="mcp", resource_metadata="https://example.com/mcp/.well-known/oauth-protected-resource", error="insufficient_scope", scope="invoices:read"'
```

**A backend refuses a caller by returning `None`.** `None` from `authenticate`
is the `401` carrying the backend's challenge. An exception a backend raises is
no longer rendered by DRF, and nothing renders it in DRF's place: Django's
`PermissionDenied` and `Http404` are Django's own HTML `403` and `404` pages,
with no challenge, and any other exception is a `500`. That includes DRF's
`AuthenticationFailed`, which was a `403` reading `{"detail": ...}` under
drf-mcp. A backend that raised it returns `None` instead.

The backend is handed an anonymous request whatever session cookie it carries,
and from the moment the request authenticates, `request.user` and
`request.auser()` are the token's principal.

## Registering operations

The drf-services registrars are gone. Declare each operation as a kernel
`ServiceSpec` or `SelectorSpec` and register the spec:

| drf-mcp | django-spec-mcp |
| --- | --- |
| `register_service_tool`, `@server.service_tool` | `register_tool(spec, *, name, ...)` with a `ServiceSpec` |
| `register_selector_tool`, `@server.selector_tool` | `register_tool(spec, *, name, ...)` with a `SelectorSpec` |
| `register_specs`, and the root `register_tools(server, definitions, *, selector_defaults=, service_defaults=)` with its `ToolDefinition`, `ToolKind`, `ServiceDefaults` and `SelectorDefaults` | `server.register_tools(registry, *, overrides=)` over a kernel `SpecRegistry`; the knobs a defaults record held are passed per tool, under its name in `overrides` |
| `register_resource(selector=...)`, `@server.resource` | `register_resource(spec, /, *, name, uri_template, ...)` with a `RETRIEVE` `SelectorSpec` |
| `register_chain_tool`, `ChainStep` | one `ServiceSpec` whose service calls the others, registered with `register_tool` |

The spec's class decides the kind of tool: a `ServiceSpec` is a service tool
and a `SelectorSpec` a selector tool.

```python
from dataclasses import dataclass

from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
)


@dataclass
class InvoiceInput:
    number: str


create_invoice_spec = ServiceSpec(
    service=create_invoice,  # def create_invoice(*, data): ...
    validator=DataclassValidator(InvoiceInput),
    presenter=DataclassPresenter(InvoiceOutput),
    permissions=[IsBillingStaff()],
)
server.register_tool(create_invoice_spec, name="invoices.create")

registry = SpecRegistry()
registry.register(
    "invoices.list",
    SelectorSpec(
        kind=SelectorKind.LIST,
        selector=list_invoices,
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[IsBillingStaff()],
    ),
)
server.register_tools(registry, overrides={"invoices.list": {"paginate": True}})

server.register_resource(
    SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=get_invoice,  # def get_invoice(*, pk): ...
        reads=Parameters([Parameter("pk", "integer", required=True)]),
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[IsBillingStaff()],
    ),
    name="invoice",
    uri_template="invoices://{pk}",
)
```

**A client sends a service tool's arguments at the top level.** The advertised
`inputSchema` is the kernel's, so the tool above takes `{"number": "A-1"}`,
with no `data` key around it. A client or an agent prompt that still wraps the
arguments in `data` gets a `validation_error` result naming what is missing,
``Missing required argument(s): `number`.``, with `data` refused as an unknown
argument in its detail.

What a binding used to declare is declared on the spec: its `reads` are the
arguments a call takes, a URL lookup among them; its `shapes` are the
read-shaping arguments a caller may send; its presenter is what a result is
rendered as; its `kwargs=` provider fills keys from the server's side. So
`ToolBinding`, `SelectorToolBinding`, `ArgumentBinding`, `QueryParam` and
`UrlKwarg` are gone, and every tool is a `KernelToolBinding`. A
`ResourceBinding` built by hand loses `kind`, `output_serializer` and
`kwargs_provider`. [Kernel specs](concepts.md#kernel-specs) is the full
account.

**A URI-template variable named `request` or `view` is refused.** Those are
the kernel's `CALL_SEEDS`, the call's own, so `register_resource` reserves them
beside the pool seeds and raises `ImproperlyConfigured` naming the resource and
the variable. Rename the variable. `TOOL_EDGE_SEEDS`, `COMPLETION_EDGE_SEEDS`
and `RESOURCE_EDGE_SEEDS` are gone; import `CALL_SEEDS` from
`django_service_specs`.

## Permissions

`MCPPermission`, `ScopeRequired`, `DjangoPermRequired` and
`DRFPermissionAdapter` are gone, and a DRF permission class gates nothing.
Every guard is a kernel `PermissionCheck` in `permissions=`, on the spec or on
a prompt or view:

| drf-mcp | django-spec-mcp |
| --- | --- |
| `ScopeRequired(["invoices:read"])` | `scopes=["invoices:read"]` at registration |
| `DjangoPermRequired("billing.view_invoice")` | a `PermissionCheck` reading `principal.has_perm("billing.view_invoice")` |
| an `MCPPermission` asked `has_permission(request, token)` | a `PermissionCheck` asked `has_permission(principal, spec)` |

```python
from django_service_specs import PermissionCheck


class CanViewInvoices(PermissionCheck):
    message = "You may not view invoices."

    def has_permission(self, principal, spec) -> bool:
        return principal.has_perm("billing.view_invoice")


server.register_prompt(
    name="summarise-ledger",
    render=render_summary,
    permissions=[CanViewInvoices()],
    scopes="ledger:read",
)
```

On a prompt or a view, two answers changed:

- **A deactivated principal is refused**, as a spec's dispatch refuses one: a
  `prompts/get` or `resources/read` of a view by a token whose user is
  inactive is answered `-32006`.
- **`permissions=[]` is refused** with `ImproperlyConfigured`, as on a spec,
  because an empty list reads as a gate and admits everyone. Leave
  `permissions=` out for no checks, or declare `[Unrestricted()]`.

[Prompts and interactive views](auth.md#prompts-and-interactive-views) has the
rest of the rule, and [Add a custom permission](recipes/custom-permission.md) a
worked check.

**Only Django's `PermissionDenied` is a denial.** Raised from a check, it is
still `-32006` under a `403`. DRF's `PermissionDenied` is not a subclass of it,
so it is now a fault like any other exception: `-32603`, logged at `ERROR`. A
check that raised DRF's class returns `False` or raises Django's.

## Prompts, completers and views

**A prompt's `render` and a completer declaring `request` receive Django's
`HttpRequest`,** not a DRF `Request`, with the caller's token on it as
`request.mcp_token`. There is no `request.data` or `request.query_params`: a
render reads the prompt's arguments by name, and a completer reads what was
typed from `value` and the client's resolved siblings from `arguments`.

**A `user` seed for a token naming no one is `AnonymousUser`**, not `None`, so
a render testing `user is None` tests `user.is_authenticated` instead.

**A completer is handed a sibling argument named `serializer`** like any other;
drf-mcp stripped it.

**A view's callable is `render=`.** `register_ui_resource(selector=...)` is now
`register_ui_resource(render=...)`; passing `selector=` is a `TypeError`.

## Tool output is JSON

TOON output is gone, along with `OutputFormat`, `encode_toon`,
`register_tool(output_format=)` and its `register_tools` override,
`KernelToolBinding.output_format`, `build_tool_result(output_format=)` and the
`toon` extra. A tool result's `content[0]` text is the payload as JSON, beside
the same payload in `structuredContent`.

- A `SPEC_MCP["DEFAULT_OUTPUT_FORMAT"]` left in settings is ignored, with no
  warning. Delete it.
- A `tools/call` carrying `outputFormat` beside `name` and `arguments` is no
  longer refused for an unknown value: the parameter is not read, and the call
  is answered in JSON. Sent inside `arguments` instead, it is an argument the
  tool does not declare, and is refused as one under `UnknownArguments.REJECT`.

## What an HTTP client sees

The endpoint classes are renamed and are plain Django views:

| drf-mcp | django-spec-mcp |
| --- | --- |
| `StreamableHttpViewSet` | `StreamableHttpView`, in `django_spec_mcp.transport.streamable_http_view` |
| `AsyncStreamableHttpViewSet` | `AsyncStreamableHttpView`, in `django_spec_mcp.transport.async_streamable_http_view` |
| `ProtectedResourceMetadataViewSet` | `ProtectedResourceMetadataView`, in `django_spec_mcp.auth.protected_resource_metadata_view` |

`as_view` takes only the collaborators as keyword arguments, with no routing
map. A project mounting `server.urls` or `server.async_urls` needs no change.

A few answers change, which matters to a test suite that pins them:

- `OPTIONS` answers `200` with `Allow` and an empty body, where it served DRF's
  metadata.
- A method the endpoint does not route is a `405` with `Allow` and an empty
  body, rather than a JSON `detail`.
- A `?format=` query is ignored rather than answered `404`.
- The `406` body and the metadata document are written with `json.dumps`
  defaults: `", "` and `": "` separators, and non-ASCII escaped.

## Calling a tool in process

`call_tool` and `acall_tool` never write the call's user to a request you hand
them: each works on a copy of `request=` whose `user` is the call's `user=`, so
a view handing its own request keeps serving as its own user after the call.
`acall_tool` handed the request itself through under drf-mcp. See
[Transport-neutral invocation](concepts.md#transport-neutral-invocation-call_tool)
for what the copy shares with the request it was made from.
