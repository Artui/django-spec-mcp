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
    """RFC 9728 OAuth 2.0 Protected Resource Metadata endpoint.

    [`MCPServer`][django_spec_mcp.server.mcp_server.MCPServer] mounts it in two
    places. ``server.urls`` mounts it under the server's own prefix, at
    ``<mount>.well-known/oauth-protected-resource``, the URL a backend's
    ``WWW-Authenticate`` challenge points at. ``server.well_known_urls()``
    mounts it at RFC 9728 3.1's location, the origin with the resource's path
    after the well-known prefix (``/.well-known/oauth-protected-resource/mcp/``
    for ``https://example.com/mcp/``), which a project adds to its root URLConf
    and where a client looks when a challenge carries no pointer. A plain Django view
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

    @staticmethod
    def well_known_route(resource: str) -> str:
        """The root-URLConf route RFC 9728 3.1 places ``resource``'s metadata at.

        The well-known prefix goes between the host and the resource's path,
        and a terminating slash right after the host is dropped, so
        ``https://example.com/mcp/`` answers at
        ``.well-known/oauth-protected-resource/mcp/`` and ``https://example.com/``
        at ``.well-known/oauth-protected-resource``. A query is left out of the
        route, since the resolver matches the path alone, and the path is
        percent-decoded, since the resolver matches the decoded ``path_info``
        (which also turns ``%2F`` into a ``/``, so ``a%2Fb`` is routed as the two
        segments ``a/b``, as Django resolves it; a front server that rejects or
        keeps ``%2F`` encoded makes such a path fragile,
        ``test_the_route_is_built_from_the_decoded_path``):
        ``https://example.com/caf%C3%A9/mcp/`` answers at
        ``.well-known/oauth-protected-resource/café/mcp/``, the URL a client
        sends in its encoded form. Built from the
        identifier the document names, the location cannot disagree with it,
        which is what a client checks (RFC 9728 3.3: the ``resource`` it reads
        must be the identifier it built the URL from).

        A resource that is not an absolute ``http(s)`` URL with a host and no
        fragment (RFC 9728 1.2) is refused with ``ImproperlyConfigured``, since
        no location can be built from it. Each condition has its case in
        ``test_a_resource_that_is_not_an_absolute_url_is_refused_by_name``. So is
        one whose path holds ``<`` or ``>``, raw or percent-encoded: not legal
        in a URI, and Django would read ``<int:x>`` in the route as a path
        converter (``test_a_resource_path_with_angle_brackets_is_refused_by_name``,
        whose encoded cases fail if the check ran before the decoding), and so is
        one whose path holds ``$``, raw or percent-encoded: ``path()`` matches it
        literally, but Django's URL check warns about a route ending in one as a
        leftover regex anchor (``test_a_resource_path_with_a_dollar_sign_is_refused_by_name``,
        whose encoded cases fail if the check ran before the decoding), and so is
        one whose percent-encoding is not valid UTF-8, which would decode to a
        route nothing matches
        (``test_a_resource_path_that_does_not_decode_as_utf8_is_refused_by_name``).
        """
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
