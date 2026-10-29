# Auth

Backends, response builders, rate limits, and the RFC 9728
protected-resource metadata view.

A tool or resource is guarded by its spec's own `permissions`, the kernel's
[`PermissionCheck`][django_service_specs.PermissionCheck], and by the
`scopes=` gate at registration; see
[Who may call a tool](../auth.md#who-may-call-a-tool). A prompt or an
interactive view takes the same kernel checks through `permissions=`, each
handed the prompt's or view's binding as the spec, and the same `scopes=`; see
[Prompts and views](../auth.md#prompts-and-interactive-views).

## Protocols

::: django_spec_mcp.auth.types.auth_backend.MCPAuthBackend
::: django_spec_mcp.auth.types.token_info.TokenInfo
::: django_spec_mcp.auth.types.self_checking.SelfChecking

## Backends

::: django_spec_mcp.auth.backends.allow_any_backend.AllowAnyBackend

This package ships no backend that verifies tokens; a project writes its own
([Writing your own backend](../auth.md#writing-your-own-backend)). A backend of
its own compares the token's audience with `audience_matches`, the exact
comparison RFC 8707 asks for, and builds its 401 challenge with
`bearer_challenge`.

::: django_spec_mcp.auth.audience.audience_matches
::: django_spec_mcp.auth.bearer_challenge.bearer_challenge

## Rate limits

::: django_spec_mcp.auth.rate_limits.types.mcp_rate_limit.MCPRateLimit
::: django_spec_mcp.auth.rate_limits.fixed_window_rate_limit.FixedWindowRateLimit
::: django_spec_mcp.auth.rate_limits.sliding_window_rate_limit.SlidingWindowRateLimit
::: django_spec_mcp.auth.rate_limits.token_bucket_rate_limit.TokenBucketRateLimit

## Response helpers

::: django_spec_mcp.auth.unauthenticated_response.build_unauthenticated_response
::: django_spec_mcp.auth.insufficient_scope_response.build_insufficient_scope_response

## Protected Resource Metadata (RFC 9728)

The view serving the document is a plain Django view, mounted under the
server's URLs as `protected-resource-metadata`.

::: django_spec_mcp.auth.protected_resource_metadata_view.ProtectedResourceMetadataView
::: django_spec_mcp.auth.types.protected_resource_metadata.ProtectedResourceMetadata
::: django_spec_mcp.auth.types.authorization_server_metadata.AuthorizationServerMetadata
