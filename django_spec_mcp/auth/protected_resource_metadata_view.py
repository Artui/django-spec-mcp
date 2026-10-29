from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.http import HttpRequest, HttpResponseBase, JsonResponse
from django.utils.decorators import classonlymethod, method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from django_spec_mcp.auth.types.auth_backend import MCPAuthBackend

# The wording the MCP endpoint answers an ``Accept`` it cannot serve with, so the
# two views refuse alike.
_NOT_ACCEPTABLE_DETAIL: str = "Could not satisfy the request Accept header."


@method_decorator(never_cache, name="dispatch")
class ProtectedResourceMetadataView(View):
    """RFC 9728 OAuth 2.0 Protected Resource Metadata endpoint.

    Mounted at ``/.well-known/oauth-protected-resource`` by
    [`MCPServer`][django_spec_mcp.server.mcp_server.MCPServer]. A plain Django view
    answering ``GET``, wired as
    ``ProtectedResourceMetadataView.as_view(auth_backend=...)``; ``HEAD`` takes
    the same handler and ``OPTIONS`` names the methods. The payload comes from the instance-scoped
    ``MCPAuthBackend.protected_resource_metadata``, so multiple servers in one process
    advertise different metadata.

    Open to everyone: PRM is a public discovery endpoint, so the view is
    CSRF-exempt and never redirected by ``LoginRequiredMiddleware``, and the MCP
    transport owns its own auth pipeline through
    [`MCPAuthBackend`][django_spec_mcp.auth.types.auth_backend.MCPAuthBackend].
    The answer is always JSON, because the payload shape is RFC-defined, and an
    ``Accept`` header admitting no JSON is refused with a ``406``."""

    auth_backend: MCPAuthBackend | None = None

    @classonlymethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., Any]:
        """Build the view function, opted out of CSRF and of the login redirect.

        The opt-outs are set as attributes, as the MCP endpoint's are, because
        the middleware reads them off the resolved callable.
        """
        view = super().as_view(**initkwargs)
        view.csrf_exempt = True  # ty: ignore[unresolved-attribute]
        view.login_required = False  # ty: ignore[unresolved-attribute]
        return view

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if not request.accepts("application/json"):
            return JsonResponse({"detail": _NOT_ACCEPTABLE_DETAIL}, status=406)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request: HttpRequest) -> JsonResponse:  # noqa: ARG002 — Django's handler signature
        if self.auth_backend is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("ProtectedResourceMetadataView is missing an MCPAuthBackend")
        return JsonResponse(self.auth_backend.protected_resource_metadata().to_dict())


__all__ = ["ProtectedResourceMetadataView"]
