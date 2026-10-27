# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This package continues `djangorestframework-mcp-server`, whose history it
carries; releases up to 0.54.0 are in
[that package's changelog](https://github.com/Artui/djangorestframework-mcp-server/blob/main/CHANGELOG.md).

## [Unreleased]

### Removed

- **The bundled authorization-server pieces, `django_spec_mcp.contrib`.** Dynamic
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
