# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This package continues `djangorestframework-mcp-server`, whose history it
carries; releases up to 0.54.0 are in
[that package's changelog](https://github.com/Artui/djangorestframework-mcp-server/blob/main/CHANGELOG.md).

## [Unreleased]

### Added

- **Kernel specs register as tools and resources.** `MCPServer.register_tool(spec, *, name, ...)`
  takes a `django_service_specs` `ServiceSpec` or `SelectorSpec`, and the spec's
  class decides whether it is a service tool or a selector tool;
  `MCPServer.register_tools(registry, *, overrides=)` registers every entry of a
  kernel `SpecRegistry`; and `register_resource` takes a `RETRIEVE` spec
  positionally, beside its `selector=` form. The call runs through the kernel's
  `dispatch` / `adispatch` with the token's user as the principal and the request,
  carrying the token as `request.mcp_token`, in `call_context`; the advertised
  schemas are the kernel's, so a service tool's arguments sit at the top level
  with no `data` key. Each kernel refusal is answered as the drf-services path
  answers its counterpart. `scopes=` gates a tool or resource on every scope it
  names, answering `-32006` with `requiredScopes`; a `KernelToolBinding` or
  `KernelResourceBinding` built by hand derives the same gate from its own
  `scopes`, and refuses `scopes=()`. Registration refuses a spec
  that is not a kernel spec, a knob of the other kind of tool, `scopes=()`,
  `UnknownArguments.PASSTHROUGH`, a resource's `uri_template` variable that no
  read or shape of the spec declares or that the server fills (a reflected read, or
  a key a typed `kwargs=` provider declares, unless its type admits `UnsetType`), each named in the error, and, for now,
  any `task_policy` but the default.
  A spec declaring `permissions=None` is refused under
  `REQUIRE_TOOL_PERMISSIONS`; with the setting off it registers with an
  `UnguardedToolWarning` and is called without a permission check, under a
  kernel `Grant` minted per call for that spec and principal. Only
  `register_tool` and `register_resource` grant, since a kernel `SpecRegistry`
  refuses such a spec before `register_tools` sees it; a kernel binding built
  by hand and passed to
  `server.tools.register` or `server.resources.register` is never granted, so
  the kernel refuses its unguarded spec at the call.
  A kernel tool's `inputSchema` leaves out every key the server fills, through
  its own pool seeds or a typed `kwargs=` or `input_data=` provider that always
  fills it, and offers without requiring a key a provider may fill; a paged
  tool's shapes say they apply to each item in the server's
  `AgentConventions.query_param_on_pages` wording. A `many` or `returns_many`
  service declared `allow_none` advertises an `outputSchema` of
  type `"array"` and serves `[]` when it presents nothing, as the drf-services
  path does for the same declaration.
  The drf-services registration methods are unchanged.
- **Listings are filtered by a kernel spec's checks.** With
  `FILTER_LISTINGS_BY_PERMISSIONS` on, a kernel binding is listed only to a
  caller its spec's checks admit, asked with no arguments through `authorize`
  and, for a create, through `authorize_scope` of `None`. A check that cannot
  answer without the call's arguments hides that binding alone, and
  `always_listed=True` keeps it visible.
- **A kernel spec's checks answer before the charge.** They are asked before
  the rate limit is charged, on `tools/call`, `acall_tool` and
  `resources/read`, so a caller they refuse is answered `-32006` uncharged;
  `call_tool` raises the denial (`NotPermitted`, or `PrincipalUnavailable` for a
  deactivated principal) before it checks the arguments. A hand-built binding
  that declares a task policy asks them before it creates a task, and its
  worker asks them again. They are shown the arguments as the kernel's argument
  check reads them, `"8"` for an `integer` as `8`, and are asked once per call
  whose arguments pass that check, twice when the call streams progress (only
  the async transport streams).
- **A call failing the argument check is judged as a listing judges the
  spec.** Its checks are asked on no arguments. Refused, it is `-32006` before
  the charge; admitted, a tool call is told which argument is wrong after the
  charge. A `many` call whose rows alone are refused keeps the answer its
  checks gave the rest of its arguments; admitted, its checks are asked twice,
  or three times when it streams. A `many` call whose Validator refuses some rows
  and also the list itself, a length cap say, is answered the `validation_error`
  result: the rows keyed by index, the list's own message under
  `non_field_errors`.
- **A streamed denial is a `403`.** A streamed `tools/call` whose spec's
  checks deny the caller is answered `403` with a challenge, as the same call
  without a progress token is, and a `ServiceError` a check raises on a streamed tool call is
  an `isError` result inside the stream, as on a plain call.
- **Completion is judged as the listing judges.** `completion/complete` of a
  kernel resource asks the spec's checks on no arguments before its rate limit.
  A check that refuses or raises on no arguments, a `ServiceError` included,
  refuses it with `-32006`; a configuration error a check raises
  (`ImproperlyConfigured`) fails the completion, as it fails the listing.
- **Resource reads answer an invalid URI variable.** An admitted reader of a
  URI whose variable fails the argument check is answered `-32602` with message
  `Invalid arguments` and the kernel's detail in `data`; `INCLUDE_VALIDATION_VALUE`
  adds the variables sent under `data.value`. The checks are shown the
  variables read as the spec's types, `true` for a `boolean` as `True`.
- **A kernel resource is watchable only by a caller who may read it.** Its
  spec's checks are asked as the read asks them.
- **A kernel tool's question is checked against the names it takes.** An
  `AdditionalInputRequired` asking for a property that the spec does not declare,
  or that the server fills for the call (a `server_arguments` name, a pool seed or
  a key a provider always fills), is answered `-32603` naming the property and the
  names the tool does take, instead of being put to the user whose answer the call
  would then refuse, drop or replace. `build_requested_schema` takes the names as
  `admitted=`; `None`, the default, asks nothing of them.
- **Kernel tool registration rules.** A kernel `LIST` tool registered without
  `paginate=True` warns `UnboundedListWarning`, and `REQUIRE_LIST_PAGINATION` refuses it. A paginated
  `LIST` whose spec declares a read named `page` or `limit` is refused, since
  pagination takes both names from the arguments before the selector runs. A
  presenter whose audience markings cannot be projected is refused, naming the
  tool. `display_name` and `display_description` are labels for in-process
  consumers, never sent on the wire. `UndescribedToolWarning` and
  `UnboundedListWarning` raised while registering a kernel spec with
  `register_tool` or `register_tools` name the line that registered it, not a
  line inside django-spec-mcp, so a warnings filter keyed on the registering
  module matches them.
- **A kernel spec whose declaration cannot hold is refused at registration, and
  a kernel tool names the arguments a call left out.** `register_tool`,
  `register_tools` and `register_resource` ask the kernel's `check_seeds` with
  the server's `pool_seeds` and refuse, naming the tool or resource, a spec no
  call could run as written: a parameter named like a registered seed or the
  call's own `request` or `view`, a positional-only parameter with no default, a
  keyword the callable requires that nothing fills, and an optional read over a
  keyword the callable requires. Each was registered and listed before, and
  failed on every call, or on every call leaving the read out. A kernel tool
  call refused for leaving arguments out is answered in the server's
  `AgentConventions.missing_arguments`, ``Missing required argument(s): `pk`.``
  by default, read off the kernel's `InvalidArguments.missing`, with the per-field
  detail as before. That includes a Validator field, which the drf-services
  path answers as `Invalid arguments`.
- **An unpaginated kernel `LIST` tool is held to the row ceiling.** Its own
  `max_page_size`, deferring to the server's `MAX_PAGE_SIZE`, is the most rows
  one result carries. A result over it is refused as `result_too_large` rather
  than truncated, since an unpaginated payload carries nothing to say rows were
  dropped. One row past the ceiling is read, as a `LIMIT` on a QuerySet and a
  slice of any other iterable, so a table is not fetched whole to be refused.
  A disabled ceiling serves the result whole.
- **A kernel tool's operation conditions are asked at list time with the seeds
  its call resolves.** `tools/list` resolves the server's `pool_seeds` per
  conditioned kernel tool against that tool's own `call_context`, as the call
  does, so a seed whose resolver declares `request`, `view` or `call_context`
  reads what the call hands it. A registered seed's resolver runs once per
  conditioned kernel tool per listing, as it runs once per call, so a seed that
  queries is paid for per tool.

### Changed

- **`django-service-specs` 0.18.0 is the floor.** Its root holds every kernel name
  this package reads, and it reserves `shapes` as a pool name, so a `PoolSeeds`
  entry named `shapes` is refused when it is built, as one named `user` is.
- **The package is `django-spec-mcp`, imported as `django_spec_mcp`.** Every
  import of `rest_framework_mcp` becomes `django_spec_mcp`, with the same module
  paths below it, `constants` apart. The settings key is `SPEC_MCP`; a `REST_FRAMEWORK_MCP` setting
  is no longer read, and nothing warns about it, so rename the key. The
  `serverInfo.name` default is `django-spec-mcp`, and the logger and tracer names
  follow the import package.
- **`rest_framework_mcp.constants` has no counterpart.** Its enums and constants
  are importable from `django_spec_mcp.types`, each from a module of its own
  (`django_spec_mcp.types.output_format` for `OutputFormat`), and the names the
  root exported from it still are. `ArgumentBinding`, `UnknownArguments` and
  `RESERVED_POOL_SEEDS` were re-exports: the root exports the first two, and
  `django_service_specs`, which defines the third, exports it.
- **Every name stored outside the process is new**: the cache and Redis
  prefixes are `spec-mcp:session:`, `spec-mcp:task:`, `spec-mcp:sse`,
  `spec-mcp:sse-replay`, `spec-mcp:sub`, `spec-mcp:rl`, `spec-mcp:rl-sw` and
  `spec-mcp:rl-tb`, and elicitation state is signed with a new salt. **The first
  deploy therefore drops every live session, in-flight task, rate-limit window and
  pending elicitation**: clients start a new session, and a pending elicitation is
  asked again.

### Removed

- **The bundled authorization-server pieces, `rest_framework_mcp.contrib`.** Dynamic
  Client Registration (`DynamicClientRegistrationViewSet` and its serializer and
  types), the authorization-server metadata and OpenID discovery views,
  `build_oauth_urlpatterns` with its `mcp-oauth-*` URL names,
  `check_oauth_url_shadowing`, `build_authorize_passthrough_view`,
  `AuthUserAdapter` and `SimpleJWTCookieAdapter` are gone, and so are the settings
  only they read: `DCR_ENABLED`, `DCR_INITIAL_ACCESS_TOKEN` and
  `SIMPLEJWT_ACCESS_COOKIE`. OAuth is the project's: run the authorization server
  you choose (django-oauth-toolkit serves its own registration and discovery
  endpoints from 3.4.0, and so does any outside identity provider), sign users in
  at its own authorize endpoint, and verify its tokens through an
  `MCPAuthBackend`. This package keeps the resource-server side: the backend
  protocol, the protected-resource metadata view and the challenge responses.

- **The refusal of settings keys drf-mcp removed in its 0.12.0.**
  `SPEC_MCP["AUTH_BACKEND"]`, `["SESSION_STORE"]` and `["AUTH_USER_ADAPTER"]` no
  longer raise `ImproperlyConfigured` at `MCPServer(...)`: no project ever set
  them under this package's settings key, so there is nothing to catch. Pass the
  collaborators to `MCPServer(auth_backend=..., session_store=...)`, as before.

- **Chain tools.** `MCPServer.register_chain_tool`, `ChainStep`, `ChainContext`
  and `ChainToolBinding` are gone, with the chain branches of `tools/list`,
  `tools/call` and the availability check. A chain ran its steps outside the
  spec dispatch every other tool goes through, so each spec-level behaviour had
  to be re-implemented for it. To run several operations as one tool, write one
  service spec whose service calls the others, and register it with
  `register_service_tool`: it is judged, validated and rendered like any other
  service tool, and its `atomic` flag owns the transaction.

- **The `jwt`, `spectacular` and `filter` extras.** `jwt` only installed
  djangorestframework-simplejwt for the cookie adapter removed above. The other
  two only re-exposed a package drf-services already reads: a tool's
  `inputSchema` still honours `@extend_schema_*` annotations, and a selector
  spec's `FilterSet` still runs. A project using either has the package
  installed already; one that does not runs `pip install drf-spectacular` or
  `pip install django-filter`.

[Unreleased]: https://github.com/Artui/django-spec-mcp/compare/v0.0.0...HEAD
