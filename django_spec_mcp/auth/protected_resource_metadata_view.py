from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponseBase, JsonResponse
from django.utils.decorators import classonlymethod, method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from django_spec_mcp.auth.types.auth_backend import MCPAuthBackend

# The wording the MCP endpoint answers an ``Accept`` it cannot serve with. It is
# restated here rather than imported from ``transport.utils`` because
# ``transport/__init__`` eagerly imports the views, which import ``auth``.
_NOT_ACCEPTABLE_DETAIL: str = "Could not satisfy the request Accept header."

# RFC 9728 3.1's well-known URI path suffix, as a route under a root URLConf.
_WELL_KNOWN_ROUTE: str = ".well-known/oauth-protected-resource"


@method_decorator(never_cache, name="dispatch")
class ProtectedResourceMetadataView(View):
    """RFC 9728 OAuth 2.0 Protected Resource Metadata endpoint."""

    auth_backend: MCPAuthBackend | None = None

    @classonlymethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., Any]:
        """Build the view function, opted out of CSRF and of the login redirect."""
        view = super().as_view(**initkwargs)
        view.csrf_exempt = True  # ty: ignore[unresolved-attribute]
        view.login_required = False  # ty: ignore[unresolved-attribute]
        return view

    @staticmethod
    def well_known_route(resource: str) -> str:
        """The root-URLConf route RFC 9728 3.1 places ``resource``'s metadata at."""
        parts = urlsplit(resource)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.fragment:
            raise ImproperlyConfigured(
                f"The protected resource metadata names the resource {resource!r}, which is "
                "not an absolute http(s) URL with a host and no fragment, so RFC 9728 gives "
                "it no well-known location. Return the server's canonical URL, such as "
                "'https://example.com/mcp/', from protected_resource_metadata()."
            )
        try:
            decoded = unquote(parts.path, errors="strict")
        except UnicodeDecodeError:
            raise ImproperlyConfigured(
                f"The protected resource metadata names the resource {resource!r}, whose path "
                "does not percent-decode as UTF-8, so no route can match a request for it. "
                "Return the server's canonical URL, such as 'https://example.com/mcp/', from "
                "protected_resource_metadata()."
            ) from None
        if "<" in decoded or ">" in decoded:
            raise ImproperlyConfigured(
                f"The protected resource metadata names the resource {resource!r}, whose path "
                "holds '<' or '>'. Neither is legal in a URI, and Django would read them in "
                "the well-known route as a path converter. Return the server's canonical URL, "
                "such as 'https://example.com/mcp/', from protected_resource_metadata()."
            )
        if "$" in decoded:
            raise ImproperlyConfigured(
                f"The protected resource metadata names the resource {resource!r}, whose path "
                "holds '$'. Django matches it literally in a path() route, but its URL check "
                "warns (2_0.W001) about a route ending in '$' as a leftover regex anchor. "
                "Return the server's canonical URL, such as 'https://example.com/mcp/', from "
                "protected_resource_metadata()."
            )
        suffix = "" if decoded in ("", "/") else decoded
        return _WELL_KNOWN_ROUTE + suffix

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if not request.accepts("application/json"):
            return JsonResponse({"detail": _NOT_ACCEPTABLE_DETAIL}, status=406)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request: HttpRequest) -> JsonResponse:  # noqa: ARG002 — Django's handler signature
        if self.auth_backend is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("ProtectedResourceMetadataView is missing an MCPAuthBackend")
        return JsonResponse(self.auth_backend.protected_resource_metadata().to_dict())


__all__ = ["ProtectedResourceMetadataView"]
