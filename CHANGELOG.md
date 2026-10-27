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
  names, answering `-32006` with `requiredScopes`. Registration refuses a spec
  that is not a kernel spec, a knob of the other kind of tool, `scopes=()`,
  `UnknownArguments.PASSTHROUGH` and, for now, any `task_policy` but the default.
  The drf-services registration methods are unchanged.

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
