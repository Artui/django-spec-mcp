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

[Unreleased]: https://github.com/Artui/django-spec-mcp/compare/v0.0.0...HEAD
