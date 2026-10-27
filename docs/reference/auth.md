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
::: django_spec_mcp.auth.types.authorization_server_metadata.AuthorizationServerMetadata
