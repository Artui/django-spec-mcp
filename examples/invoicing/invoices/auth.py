"""A demo bearer backend: two hard-coded tokens, for this example only.

The shape is the one ``docs/auth.md`` walks through (``Writing your own
backend``): read the bearer token, answer ``None`` for one that does not
verify, publish the RFC 9728 metadata document, and build the challenge with
``bearer_challenge``. What a real backend does in ``authenticate`` instead of
the dictionary lookup is verify the token its authorization server issued (a
JWKS check, an introspection call), resolve its user, and refuse a token issued
for another resource with ``audience_matches``.

DO NOT deploy this. Anyone who reads this file holds both tokens.
"""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest

from django_spec_mcp.auth import TokenInfo, bearer_challenge
from django_spec_mcp.auth.types.protected_resource_metadata import ProtectedResourceMetadata

# Where ``manage.py runserver`` serves the mount, which is what a client is
# told this resource is.
RESOURCE_URL = "http://localhost:8000/mcp/"

# The demo's whole "authorization server": token to the scopes it carries.
# ``invoices:email`` is what the ``compose_invoice_email`` prompt asks for, so
# the clerk may render it and the intern is refused with a 403 naming it.
DEMO_TOKENS: dict[str, tuple[str, ...]] = {
    "demo-clerk": ("invoices:email",),
    "demo-intern": (),
}


class DemoTokenBackend:
    """Authenticates the two demo tokens and nothing else."""

    def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        scheme, _, raw = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or raw not in DEMO_TOKENS:
            # ``None`` is the 401, carrying the challenge below.
            return None
        # No user is resolved: the demo has none, and every spec here is
        # ``Unrestricted()``, so only the scopes decide anything.
        return TokenInfo(
            user=AnonymousUser(), scopes=DEMO_TOKENS[raw], audience=RESOURCE_URL, raw=raw
        )

    def protected_resource_metadata(self) -> ProtectedResourceMetadata:
        # No authorization server issues these tokens, so none is named. A
        # real backend names the one whose tokens it verifies.
        return ProtectedResourceMetadata(
            resource=RESOURCE_URL,
            authorization_servers=[],
            scopes_supported=["invoices:email"],
        )

    def www_authenticate_challenge(
        self, *, scopes: list[str] | None = None, error: str | None = None
    ) -> str:
        # The server mounts the metadata view under its own URL, so the
        # pointer a client follows is derived from it.
        return bearer_challenge(
            resource_metadata_url=f"{RESOURCE_URL}.well-known/oauth-protected-resource",
            scopes=scopes,
            error=error,
        )
