from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBase,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.utils.decorators import classonlymethod
from django.views import View
from django_service_specs import DEFAULT_POOL_SEEDS, PoolSeeds

from django_spec_mcp._compat.reject_awaitable import reject_awaitable
from django_spec_mcp.auth.principal_for_token import principal_for_token
from django_spec_mcp.auth.types.auth_backend import MCPAuthBackend
from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.dispatch import dispatch
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.types.exception_handler import ExceptionHandler
from django_spec_mcp.observability.get_logger import get_logger
from django_spec_mcp.protocol.types.implementation import Implementation
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.json_rpc_message import JsonRpcMessage
from django_spec_mcp.protocol.types.json_rpc_notification import JsonRpcNotification
from django_spec_mcp.protocol.types.json_rpc_request import JsonRpcRequest
from django_spec_mcp.registry.prompt_registry import PromptRegistry
from django_spec_mcp.registry.resource_registry import ResourceRegistry
from django_spec_mcp.registry.tool_registry import ToolRegistry
from django_spec_mcp.schema.types.agent_conventions import AgentConventions
from django_spec_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from django_spec_mcp.tasks.types.task_executor import TaskExecutor
from django_spec_mcp.tasks.types.task_store import TaskStore
from django_spec_mcp.transport.negotiate_protocol_version import negotiate_protocol_version
from django_spec_mcp.transport.types.request_metadata import RequestMetadata
from django_spec_mcp.transport.types.session_store import SessionStore
from django_spec_mcp.transport.utils import (
    allowed_methods,
    carry_principal,
    error_response,
    exempt_endpoint,
    internal_error_response,
    is_raised_denial,
    legacy_result_response,
    modern_era_requested,
    modern_header_refusal,
    modern_result_response,
    not_acceptable,
    origin_refusal,
    params_dict,
    raised_denial_error,
    read_message,
    seat_anonymous,
    session_rejection,
    unauthenticated_response,
    unknown_session_response,
    unsupported_version_response,
)
from django_spec_mcp.transport.validate_modern_request import (
    VERSION_HEADER,
)
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.sessionless_methods import SESSIONLESS_METHODS

_SESSION_HEADER: str = "Mcp-Session-Id"
# WSGI never streams, so JSON is all this view answers a POST, DELETE or
# OPTIONS with, and a client accepting only ``text/event-stream`` there cannot
# be served (``test_the_sync_endpoint_refuses_an_accept_naming_only_a_stream``).
_ANSWERS_WITH: tuple[str, ...] = ("application/json",)
# Answered with a bodyless ``405`` whatever the ``Accept``, so never refused
# for it: the spec has a GET list ``text/event-stream`` and a server that
# offers no stream answer ``405``, which negotiating first turned into a ``406``
# (``test_a_sync_get_naming_only_a_stream_is_the_405_not_a_406``). ``head``
# because Django routes it to the ``get`` handler.
_UNNEGOTIATED: frozenset[str] = frozenset({"GET", "HEAD"})

logger = get_logger(__name__)


def _reject_awaitable_token(result: Any, *, backend: Any) -> Any:
    """Return the token, or refuse an ``authenticate`` that must be awaited."""
    return reject_awaitable(
        result,
        call=f"{type(backend).__name__}.authenticate()",
        remedy=(
            "This request was served by the sync MCP transport (server.urls), which "
            "cannot await it. Mount the server under server.async_urls (ASGI) so the "
            "backend is awaited, or make authenticate a plain 'def'."
        ),
        hazard=(
            "an un-awaited coroutine is truthy, so serving it would authenticate every caller."
        ),
    )


def _reject_awaitable_session(result: Any, *, store: Any, method: str) -> Any:
    """Return a session-store answer, or refuse one that must be awaited."""
    return reject_awaitable(
        result,
        call=f"{type(store).__name__}.{method}()",
        remedy=(
            "Session stores may be async only under the async transport, which awaits "
            "them; this request was served by the sync transport (server.urls). Mount "
            f"the server under server.async_urls (ASGI), or make {method} a plain 'def'."
        ),
        hazard=(
            "an un-awaited coroutine matches no principal and is not a usable session "
            "id, so every subsequent request would be rejected as un-initialized."
        ),
    )


class StreamableHttpView(View):
    """The single ``/mcp`` endpoint per MCP 2025-11-25 (Streamable HTTP)."""

    # Django's ``as_view`` requires kwargs to be existing class attributes.
    tools: ToolRegistry | None = None
    resources: ResourceRegistry | None = None
    prompts: PromptRegistry | None = None
    auth_backend: MCPAuthBackend | None = None
    session_store: SessionStore | None = None
    # ``None`` on both means this server runs no tasks.
    task_store: TaskStore | None = None
    task_executor: TaskExecutor | None = None
    subscription_broker: SubscriptionBroker | None = None
    server_info: Implementation | None = None
    instructions: str | None = None
    config: MCPConfig | None = None
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS
    conventions: AgentConventions = AgentConventions()
    exception_map: Mapping[type[Exception], ExceptionHandler] = MappingProxyType({})

    @classonlymethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., Any]:
        """Build the view function, opted out of CSRF and of the login redirect."""
        return exempt_endpoint(super().as_view(**initkwargs))

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if request.method not in _UNNEGOTIATED:
            refused: JsonResponse | None = not_acceptable(request, _ANSWERS_WITH)
            if refused is not None:
                return refused
        return super().dispatch(request, *args, **kwargs)

    def post(self, http_request: HttpRequest) -> HttpResponse:
        """POST action: parse a single JSON-RPC message and dispatch."""
        config: MCPConfig = self._require_config()
        refused: JsonResponse | None = origin_refusal(http_request, config)
        if refused is not None:
            return refused

        message: JsonRpcMessage | JsonResponse = read_message(http_request, config)
        if isinstance(message, JsonResponse):
            return message

        is_initialize: bool = isinstance(message, JsonRpcRequest) and message.method == "initialize"
        is_sessionless: bool = (
            isinstance(message, JsonRpcRequest) and message.method in SESSIONLESS_METHODS
        )

        metadata: RequestMetadata | None = RequestMetadata.from_params(
            params_dict(getattr(message, "params", None))
        )
        if metadata is not None:
            return self._handle_modern(http_request, message, metadata)

        negotiated: str | None = negotiate_protocol_version(
            http_request.headers.get(VERSION_HEADER), is_sessionless=is_sessionless, config=config
        )
        if negotiated is None:
            return unsupported_version_response(getattr(message, "id", None))

        token = self._authenticate(http_request)
        if token is None:
            logger.warning("Authentication failed for %s", http_request.path)
            return unauthenticated_response(self._require_auth_backend(), http_request)

        store = self._require_session_store()
        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        principal: str = principal_for_token(token)
        if self._sessions_enabled() and not is_sessionless:
            owner_matches = (
                bool(session_id)
                and _reject_awaitable_session(store.owner(session_id), store=store, method="owner")
                == principal
            )
            rejected: JsonResponse | None = session_rejection(
                session_id,
                owner_matches=owner_matches,
                principal=principal,
                message=message,
                logger=logger,
            )
            if rejected is not None:
                return rejected

        context: MCPCallContext = self._context(
            http_request, token, protocol_version=negotiated, session_id=session_id
        )

        if isinstance(message, JsonRpcNotification):
            return HttpResponse(status=202)

        if not isinstance(message, JsonRpcRequest):
            return error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST, message="Expected a JSON-RPC request"
            )

        try:
            result: Any = dispatch(message.method, params_dict(message.params), context)
        except Exception as exc:  # noqa: BLE001 — the backstop; see ``internal_error_response``
            if not is_raised_denial(exc):
                return internal_error_response(message, logger)
            result = raised_denial_error()

        http_response: JsonResponse = legacy_result_response(
            message.id, result, self._require_auth_backend()
        )
        if is_initialize and self._sessions_enabled() and not isinstance(result, JsonRpcError):
            new_session: str = _reject_awaitable_session(
                store.create(principal_id=principal), store=store, method="create"
            )
            http_response[_SESSION_HEADER] = new_session
        return http_response

    def _handle_modern(
        self, http_request: Any, message: Any, metadata: RequestMetadata
    ) -> HttpResponse:
        """Serve one request under the stateless (``2026-07-28``) rules."""
        refused: JsonResponse | None = modern_header_refusal(
            http_request, message, metadata, self._require_config()
        )
        if refused is not None:
            return refused

        token = self._authenticate(http_request)
        if token is None:
            return unauthenticated_response(self._require_auth_backend(), http_request)

        context: MCPCallContext = self._context(
            http_request,
            token,
            protocol_version=metadata.protocol_version,
            session_id=None,
            client_capabilities=metadata.client_capabilities,
        )

        if isinstance(message, JsonRpcNotification):
            return HttpResponse(status=202)

        try:
            result: Any = dispatch(message.method, params_dict(message.params), context)
        except Exception as exc:  # noqa: BLE001 — the backstop; see ``internal_error_response``
            if not is_raised_denial(exc):
                return internal_error_response(message, logger)
            result = raised_denial_error()
        return modern_result_response(message.id, result, self._require_auth_backend())

    def get(self, http_request: HttpRequest) -> HttpResponse:
        """GET action: SSE-from-server isn't implemented in v1; 405 per spec."""
        refused: JsonResponse | None = origin_refusal(http_request, self._require_config())
        if refused is not None:
            return refused
        return self._method_not_allowed()

    def delete(self, http_request: HttpRequest) -> HttpResponse:
        """DELETE action: end the session named by ``MCP-Session-Id``."""
        refused: JsonResponse | None = origin_refusal(http_request, self._require_config())
        if refused is not None:
            return refused
        if modern_era_requested(http_request) or not self._sessions_enabled():
            return self._method_not_allowed()
        token = self._authenticate(http_request)
        if token is None:
            return unauthenticated_response(self._require_auth_backend(), http_request)
        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        if session_id:
            store = self._require_session_store()
            owner = _reject_awaitable_session(store.owner(session_id), store=store, method="owner")
            if owner != principal_for_token(token):
                return unknown_session_response()
            _reject_awaitable_session(store.destroy(session_id), store=store, method="destroy")
        return HttpResponse(status=204)

    def _context(
        self,
        http_request: HttpRequest,
        token: Any,
        *,
        protocol_version: str,
        session_id: str | None,
        client_capabilities: dict[str, Any] | None = None,
    ) -> MCPCallContext:
        """The context a dispatch reads, from this view's collaborators."""
        return MCPCallContext(
            http_request=http_request,
            token=token,
            tools=self._require_tools(),
            resources=self._require_resources(),
            prompts=self._require_prompts(),
            protocol_version=protocol_version,
            session_id=session_id,
            client_capabilities=client_capabilities if client_capabilities is not None else {},
            server_info=self.server_info,
            instructions=self.instructions,
            tasks=self.task_store,
            task_executor=self.task_executor,
            subscriptions=self.subscription_broker,
            config=self._require_config(),
            pool_seeds=self.pool_seeds,
            conventions=self.conventions,
            exception_map=self.exception_map,
        )

    def _method_not_allowed(self) -> HttpResponse:
        """A ``405`` naming the methods this view routes, as RFC 9110 requires."""
        return HttpResponseNotAllowed(allowed_methods(self))

    def _authenticate(self, http_request: HttpRequest) -> Any:
        """Resolve the caller's token; on success the request is the token's."""
        backend: MCPAuthBackend = self._require_auth_backend()
        seat_anonymous(http_request)
        token: Any = _reject_awaitable_token(backend.authenticate(http_request), backend=backend)
        if token is not None:
            carry_principal(http_request, token)
        return token

    def _require_tools(self) -> ToolRegistry:
        if self.tools is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("StreamableHttpView is missing a ToolRegistry")
        return self.tools

    def _require_resources(self) -> ResourceRegistry:
        if self.resources is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("StreamableHttpView is missing a ResourceRegistry")
        return self.resources

    def _require_prompts(self) -> PromptRegistry:
        if self.prompts is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("StreamableHttpView is missing a PromptRegistry")
        return self.prompts

    def _require_auth_backend(self) -> MCPAuthBackend:
        if self.auth_backend is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("StreamableHttpView is missing an MCPAuthBackend")
        return self.auth_backend

    def _require_config(self) -> MCPConfig:
        if self.config is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("StreamableHttpView is missing an MCPConfig")
        return self.config

    def _sessions_enabled(self) -> bool:
        """Whether the legacy era mints and requires an ``Mcp-Session-Id``."""
        return self._require_config().sessions_enabled

    def _require_session_store(self) -> SessionStore:
        if self.session_store is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("StreamableHttpView is missing a SessionStore")
        return self.session_store


__all__ = ["StreamableHttpView"]
