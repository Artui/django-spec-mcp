# Add a custom permission

A tool or resource is guarded by its spec's `permissions`: kernel
`PermissionCheck` instances, asked of the principal the auth backend resolved,
before the target is looked up or the service runs. A custom permission is a
subclass with a `has_permission(principal, spec)` method. Every check on the
spec must allow, and a refusal is answered `-32006` with HTTP 403.

## Example: tenant-scoped access

A multi-tenant app might want to gate every tool by the requesting user's
tenant matching a configured value:

```python
from django_service_specs import PermissionCheck


class TenantMatches(PermissionCheck):
    def __init__(self, tenant_id: int) -> None:
        self.tenant_id = tenant_id

    def has_permission(self, principal, spec) -> bool:
        return getattr(principal, "tenant_id", None) == self.tenant_id
```

Stack it with a `scopes=` gate to keep both surfaces honest. The `tenant_id` is
captured when the spec is built — read it from settings or any other
process-wide source, never from a `request` (no request exists then):

```python
from django.conf import settings
from django_service_specs import ServiceSpec

server.register_tool(
    ServiceSpec(
        service=refund_invoice,
        permissions=[TenantMatches(tenant_id=settings.ACTIVE_TENANT_ID)],
    ),
    name="invoices.refund",
    description="Refund an invoice.",
    scopes=("invoices:write",),
)
```

The scope gate is answered first, with the missing scopes in the challenge; a
caller holding them whose tenant does not match gets a 403 whose challenge
names no scope, because none would help. A check is the kernel's, so the same
spec is guarded the same way on every transport that serves it.

A check that needs more than the principal declares it by keyword:
`arguments` for the call's shape-checked arguments, `call_context` for the
request (and the token on it, as `request.mcp_token`), or a registered pool
seed by its name. See [Authentication](../auth.md#who-may-call-a-tool).

## Checks are synchronous, on both transports

`has_permission`, `has_object_permission` and `has_scope_permission` must be
plain `def`s. A check declaring an `async def` method is refused with
`ImproperlyConfigured` when the spec is built, under ASGI just as much as under
WSGI.

That is not an oversight of the async transport. The async path runs a check in
the executor, so an `async def has_permission` would answer with a coroutine
that nothing awaits — and an un-awaited coroutine is truthy, which means
**every caller is granted**. The refusal converts a silent, total bypass into a
misconfiguration you can see.

If a check genuinely needs to await something, do it inside the synchronous
method:

```python
from asgiref.sync import async_to_sync
from django_service_specs import PermissionCheck


class TenantMatches(PermissionCheck):
    def has_permission(self, principal, spec) -> bool:
        return async_to_sync(self._check)(principal)

    async def _check(self, principal) -> bool: ...
```

The same rule holds for [rate limiters](rate-limiting.md) (`consume`). Session
stores are the deliberate exception — see
[Swap the session store](swap-session-store.md) — and may be `async def`, but
only when mounted under `server.async_urls`; the sync transport refuses those
too rather than treating the coroutine as an answer.

## Gating a prompt or a view

A prompt and an interactive view are not specs, so their gate is the
`permissions=[...]` of `register_prompt` and `register_ui_resource`: objects
implementing the [`MCPPermission`](../reference/auth.md) Protocol, anything with
`has_permission(request, token)` and, optionally, `required_scopes()`. They are
AND-combined per binding and evaluated after authentication, and any
`required_scopes()` from a denying object are surfaced in the
`WWW-Authenticate` header.

```python
from django.http import HttpRequest

from django_spec_mcp import TokenInfo


class PromptTenantMatches:
    def __init__(self, tenant_id: int) -> None:
        self._tenant_id = tenant_id

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        return getattr(token.user, "tenant_id", None) == self._tenant_id

    def required_scopes(self) -> list[str]:
        return []
```

`has_permission` — and the optional `is_listable(token)` — must be a plain
`def` here too: writing either `async def` raises `ImproperlyConfigured` at
evaluation time, for the reason above.

## Tips

- **Cheap to construct, side-effect-free at evaluation.** A check is built once,
  with its spec, so any expensive lookup belongs in `has_permission` (and even
  then, cache it).
- **A value passed to `__init__` is read once, when the spec is built.** That
  suits a fixed deployment value like the tenant above. For one a test overrides
  or that changes while the process runs, read it inside `has_permission`
  instead, where every call sees the current value.
- **Surface scopes only when they're actually scope-shaped.** A row-level rule
  like `TenantMatches` belongs on the spec, not in `scopes=`: the failing client
  can't fix it by obtaining a different scope.
