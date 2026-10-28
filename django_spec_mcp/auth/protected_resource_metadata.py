from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from django.http import HttpRequest, HttpResponseBase, JsonResponse
from django.utils.decorators import classonlymethod, method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from django_spec_mcp.auth.types.auth_backend import MCPAuthBackend

# Worded as the endpoint has always answered an ``Accept`` it cannot serve.
_NOT_ACCEPTABLE_DETAIL: str = "Could not satisfy the request Accept header."


@method_decorator(never_cache, name="dispatch")
class ProtectedResourceMetadataViewSet(View):
    """RFC 9728 OAuth 2.0 Protected Resource Metadata endpoint.

    Mounted at ``/.well-known/oauth-protected-resource`` by
    [`MCPServer`][django_spec_mcp.server.mcp_server.MCPServer]. A plain Django view
    whose ``GET`` is the ``list`` action, wired as
    ``ProtectedResourceMetadataViewSet.as_view({"get": "list"}, auth_backend=...)``
    so the URL conf doesn't need a router; ``HEAD`` takes the same handler and
    ``OPTIONS`` names the methods. The payload comes from the instance-scoped
    ``MCPAuthBackend.protected_resource_metadata``, so multiple servers in one process
    advertise different metadata.

    Open to everyone: PRM is a public discovery endpoint, so the view is
    CSRF-exempt and never redirected by ``LoginRequiredMiddleware``, and the MCP
    transport owns its own auth pipeline through
    [`MCPAuthBackend`][django_spec_mcp.auth.types.auth_backend.MCPAuthBackend].
    The answer is always JSON, because the payload shape is RFC-defined, and an
    ``Accept`` header admitting no JSON is refused with a ``406``."""

    actions: Mapping[str, str] = {"get": "list"}
    auth_backend: MCPAuthBackend | None = None

    @classonlymethod
    def as_view(
        cls, actions: Mapping[str, str] | None = None, **initkwargs: Any
    ) -> Callable[..., Any]:
        """Build the view function routing ``actions``, ``{"get": "list"}`` if ``None``.

        The opt-outs are set as attributes, as the MCP endpoint's are, because
        the middleware reads them off the resolved callable.
        """
        routed: Mapping[str, str] = cls.actions if actions is None else actions
        view = super().as_view(actions=routed, **initkwargs)
        view.csrf_exempt = True  # ty: ignore[unresolved-attribute]
        view.login_required = False  # ty: ignore[unresolved-attribute]
        return view

    def setup(self, request: HttpRequest, *args: Any, **kwargs: Any) -> None:
        # The MCP endpoint's ``route_actions`` in ``transport/utils``, written
        # out: importing it from ``auth/`` closes a cycle, because the
        # transport's ``__init__`` imports the views and they import ``auth``.
        for method, action in self.actions.items():
            setattr(self, method, getattr(self, action))
        super().setup(request, *args, **kwargs)

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if not request.accepts("application/json"):
            return JsonResponse({"detail": _NOT_ACCEPTABLE_DETAIL}, status=406)
        return super().dispatch(request, *args, **kwargs)

    def list(self, request: HttpRequest) -> JsonResponse:  # noqa: ARG002 — routed action signature
        if self.auth_backend is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("ProtectedResourceMetadataViewSet is missing an MCPAuthBackend")
        return JsonResponse(self.auth_backend.protected_resource_metadata().to_dict())


__all__ = ["ProtectedResourceMetadataViewSet"]
