# Auth

Backends, permissions, response builders, rate limits, and OAuth-related views
(RFC 9728 PRM + the opt-in contrib `oauth/` mount).

## Protocols

::: django_spec_mcp.auth.types.auth_backend.MCPAuthBackend
::: django_spec_mcp.auth.types.token_info.TokenInfo
::: django_spec_mcp.auth.permissions.types.mcp_permission.MCPPermission

## Backends

::: django_spec_mcp.auth.backends.allow_any_backend.AllowAnyBackend
::: django_spec_mcp.auth.backends.django_oauth_toolkit_backend.DjangoOAuthToolkitBackend

## Permissions

::: django_spec_mcp.auth.permissions.scope_required.ScopeRequired
::: django_spec_mcp.auth.permissions.django_perm_required.DjangoPermRequired
::: django_spec_mcp.auth.permissions.drf_permission_adapter.DRFPermissionAdapter

## Rate limits

::: django_spec_mcp.auth.rate_limits.types.mcp_rate_limit.MCPRateLimit
::: django_spec_mcp.auth.rate_limits.fixed_window_rate_limit.FixedWindowRateLimit
::: django_spec_mcp.auth.rate_limits.sliding_window_rate_limit.SlidingWindowRateLimit
::: django_spec_mcp.auth.rate_limits.token_bucket_rate_limit.TokenBucketRateLimit

## Response helpers

::: django_spec_mcp.auth.unauthenticated_response.build_unauthenticated_response
::: django_spec_mcp.auth.insufficient_scope_response.build_insufficient_scope_response

## Protected Resource Metadata (RFC 9728)

::: django_spec_mcp.auth.protected_resource_metadata.ProtectedResourceMetadataViewSet
::: django_spec_mcp.auth.types.protected_resource_metadata.ProtectedResourceMetadata

## OAuth contrib (opt-in)

`build_oauth_urlpatterns(*, server, include_dcr=False, include_aliases=True,
include_openid_discovery=True)` returns a list of URL patterns ready to mount
alongside your `MCPServer.urls`. Exposes RFC 8414 / OIDC discovery /
RFC 7591 Dynamic Client Registration + the alias paths different LLM hosts
probe (aliases render the canonical payload — they are not HTTP redirects).

::: django_spec_mcp.contrib.oauth.build_oauth_urlpatterns.build_oauth_urlpatterns
::: django_spec_mcp.contrib.oauth.authorization_server_metadata_viewset.AuthorizationServerMetadataViewSet
::: django_spec_mcp.contrib.oauth.openid_discovery_viewset.OpenIDDiscoveryViewSet
::: django_spec_mcp.contrib.oauth.dynamic_client_registration_viewset.DynamicClientRegistrationViewSet
::: django_spec_mcp.contrib.oauth.dcr_serializer.DynamicClientRegistrationSerializer
::: django_spec_mcp.auth.types.authorization_server_metadata.AuthorizationServerMetadata
::: django_spec_mcp.contrib.oauth.types.openid_discovery_payload.OpenIDDiscoveryPayload
::: django_spec_mcp.contrib.oauth.types.dynamic_client_registration_request.DynamicClientRegistrationRequest
::: django_spec_mcp.contrib.oauth.types.dynamic_client_registration_response.DynamicClientRegistrationResponse

**DCR is deprecated by MCP revision `2026-07-28`**, in favour of Client ID
Metadata Documents, with an earliest removal of the first specification revision
released on or after 2027-07-28. It is kept, and stays off by default; the
timetable and the reasoning are on `DynamicClientRegistrationViewSet` above and
in [Authentication](../auth.md#oauth-contrib-mount).

DCR is gated by two `build_oauth_urlpatterns` arguments, each defaulting to the
matching setting when omitted:

- `dcr_enabled` (default: `SPEC_MCP["DCR_ENABLED"]`, itself `False`) —
  the DCR endpoint refuses every request while disabled.
- `dcr_initial_access_token` (default:
  `SPEC_MCP["DCR_INITIAL_ACCESS_TOKEN"]`, itself `None`) — optional
  bearer required on the DCR POST, per RFC 7591 §3.

## User-adapter hook (cookie-session bridge for `/authorize`)

::: django_spec_mcp.contrib.oauth.adapters.types.auth_user_adapter.AuthUserAdapter
::: django_spec_mcp.contrib.oauth.adapters.simplejwt_cookie.SimpleJWTCookieAdapter
