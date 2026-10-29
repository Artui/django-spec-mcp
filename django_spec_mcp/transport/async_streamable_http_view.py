from __future__ import annotations

import dataclasses
from collections.abc import Callable
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
from django_service_specs import DEFAULT_POOL_SEEDS, PoolSeeds, ProgressReporter

from django_spec_mcp._compat.acall import acall
from django_spec_mcp.auth.principal_for_token import principal_for_token
from django_spec_mcp.auth.types.auth_backend import MCPAuthBackend
from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.async_dispatch import adispatch
from django_spec_mcp.handlers.tasks_utils import (
    declares_tasks_extension,
    missing_capability_error,
)
from django_spec_mcp.handlers.types.context import MCPCallContext
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
from django_spec_mcp.subscriptions.grant_subscription import grant_subscription
from django_spec_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from django_spec_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from django_spec_mcp.subscriptions.types.subscription_filter import SubscriptionFilter
from django_spec_mcp.tasks.types.task_executor import TaskExecutor
from django_spec_mcp.tasks.types.task_store import TaskStore
from django_spec_mcp.transport.negotiate_protocol_version import negotiate_protocol_version
from django_spec_mcp.transport.progress_dispatch import (
    can_report_progress,
    preflight_permissions,
    progress_token,
    stream_with_progress,
)
from django_spec_mcp.transport.sse_response import build_sse_response
from django_spec_mcp.transport.subscription_stream import build_subscription_stream
from django_spec_mcp.transport.types.request_metadata import RequestMetadata
from django_spec_mcp.transport.types.session_store import SessionStore
from django_spec_mcp.transport.types.sse_broker import SSEBroker
from django_spec_mcp.transport.types.sse_replay_buffer import SSEReplayBuffer
from django_spec_mcp.transport.utils import (
    allowed_methods,
    carry_principal,
    challenged_error_response,
    error_response,
    exempt_endpoint,
    internal_error_response,
    is_raised_denial,
    legacy_result_response,
    modern_era_requested,
    modern_header_refusal,
    modern_refusal,
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
from django_spec_mcp.types.subscriptions_listen_method import SUBSCRIPTIONS_LISTEN_METHOD

_SESSION_HEADER: str = "Mcp-Session-Id"
# JSON, and the server-sent events a GET, a subscription or a streamed call
# answers with. ``text/event-stream`` alone is the ``Accept`` the spec tells a
# client to send for the GET stream, so it is served rather than refused
# (``test_an_accept_naming_only_a_stream_reaches_the_async_endpoint``).
_ANSWERS_WITH: tuple[str, ...] = ("application/json", "text/event-stream")

logger = get_logger(__name__)


def _capacity_refusal(cap: int, what: str, setting: str, *, request_id: Any = None) -> JsonResponse:
    """The ``503`` for a stream past its per-worker cap, naming the setting to raise.

    Refused rather than queued: each stream parks a worker for its lifetime,
    so accepting past the cap trades a clear error for a server that stops
    answering anything at all.
    """
    return error_response(
        code=JsonRpcErrorCode.INTERNAL_ERROR,
        message=(
            f"This server is already serving its maximum of {cap} concurrent "
            f"{what}. Retry shortly, or raise SPEC_MCP['{setting}']."
        ),
        status=503,
        request_id=request_id,
    )


class AsyncStreamableHttpView(View):
    """Async sibling of ``StreamableHttpView`` for ASGI deployments.

    A plain Django view whose methods are all ``async def``, so Django awaits it
    with no thread hop. Wire behaviour matches the sync view for POST and DELETE — same headers, same
    status codes, same JSON-RPC shapes — and the reasoning behind the shared checks
    lives there. The async path additionally serves server-initiated SSE on GET when an
    [`SSEBroker`][django_spec_mcp.transport.types.sse_broker.SSEBroker] is wired in;
    without one, GET returns 405, which the spec allows when there is nothing to push.

    Sync collaborators (auth backend, session store, permissions) are bridged
    through ``asgiref.sync.sync_to_async``, so a fully sync stack still
    works under ASGI.
    """

    tools: ToolRegistry | None = None
    resources: ResourceRegistry | None = None
    prompts: PromptRegistry | None = None
    auth_backend: MCPAuthBackend | None = None
    session_store: SessionStore | None = None
    # Serving a subscription is async-only: it is a stream that stays open,
    # which a sync WSGI view cannot hold. The sync view is given one only to
    # publish to it.
    subscription_broker: SubscriptionBroker | None = None
    # ``None`` on both means this server runs no tasks.
    task_store: TaskStore | None = None
    task_executor: TaskExecutor | None = None
    sse_broker: SSEBroker | None = None
    sse_replay_buffer: SSEReplayBuffer | None = None
    # Identity the owning server resolved at construction. Unlike the
    # collaborators above these stay optional at dispatch: a hand-wired view
    # with no server still answers ``initialize``, falling back to
    # ``SERVER_INFO``.
    server_info: Implementation | None = None
    instructions: str | None = None
    config: MCPConfig | None = None
    # The owning server's ``pool_seeds=``, read into every context this view
    # builds. The kernel's default for a hand-wired view, which is exactly
    # the seeds dispatch would use with none passed.
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS
    # The owning server's ``conventions=``, read into every context this view
    # builds, so the wording it serves is that server's. The defaults for a
    # hand-wired view, which is what a server given none would say.
    conventions: AgentConventions = AgentConventions()

    @classonlymethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., Any]:
        """Build the view function, opted out of CSRF and of the login redirect.

        Django marks the function a coroutine function because every method the
        class answers is ``async def``, so the handler awaits it.
        """
        return exempt_endpoint(super().as_view(**initkwargs))

    async def dispatch(  # ty: ignore[invalid-method-override]
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        refused: JsonResponse | None = not_acceptable(request, _ANSWERS_WITH)
        if refused is not None:
            return refused
        # Every route is a coroutine here, Django's ``options`` and ``405``
        # included, because ``view_is_async`` is set; the stub types the sync
        # base's return, not the coroutine it hands back.
        routed: Any = super().dispatch(request, *args, **kwargs)
        return await routed

    # ----- the methods the endpoint serves -----

    async def post(self, http_request: HttpRequest) -> HttpResponseBase:
        config: MCPConfig = self._require_config()
        refused: JsonResponse | None = origin_refusal(http_request, config)
        if refused is not None:
            return refused

        message: JsonRpcMessage | JsonResponse = read_message(http_request, config)
        if isinstance(message, JsonResponse):
            return message

        is_initialize: bool = isinstance(message, JsonRpcRequest) and message.method == "initialize"
        # Answerable without a session, but does *not* mint one — hence a
        # second flag rather than a widening of the first.
        is_sessionless: bool = (
            isinstance(message, JsonRpcRequest) and message.method in SESSIONLESS_METHODS
        )

        # **The era fork** — see the sync sibling for the reasoning.
        metadata: RequestMetadata | None = RequestMetadata.from_params(
            params_dict(getattr(message, "params", None))
        )
        if metadata is not None:
            return await self._handle_modern(http_request, message, metadata)

        negotiated: str | None = negotiate_protocol_version(
            http_request.headers.get(VERSION_HEADER), is_sessionless=is_sessionless, config=config
        )
        if negotiated is None:
            return unsupported_version_response(getattr(message, "id", None))

        # Before the session lookup, so session ids cannot be probed through a
        # 404-vs-401 oracle. See the sync sibling.
        token = await self._authenticate(http_request)
        if token is None:
            logger.warning("Authentication failed for %s", http_request.path)
            return unauthenticated_response(self._require_auth_backend())

        # A wrong-principal presentation renders the same 404 as an unknown id,
        # so the gate is not an ownership oracle.
        store = self._require_session_store()
        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        principal: str = principal_for_token(token)
        if self._sessions_enabled() and not is_sessionless:
            owner_matches = bool(session_id) and await acall(store.owner, session_id) == principal
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

        # The stream decision sits inside the backstop with the dispatch: its
        # permission pre-flight asks the binding's scope gate and the spec's
        # PermissionChecks as the dispatch would, so a raise there is the same
        # server fault, and it happens before any stream has committed a status.
        try:
            streamed: HttpResponseBase | None = await self._maybe_stream(message, context)
            if streamed is not None:
                return streamed
            result: Any = await adispatch(message.method, params_dict(message.params), context)
        except Exception as exc:  # noqa: BLE001 — the backstop; see ``internal_error_response``
            if not is_raised_denial(exc):
                return internal_error_response(message, logger)
            # A denial raised rather than returned; see ``raised_denial_error``.
            result = raised_denial_error()

        http_response: JsonResponse = legacy_result_response(
            message.id, result, self._require_auth_backend()
        )
        if is_initialize and self._sessions_enabled() and not isinstance(result, JsonRpcError):
            # Assignment is the server's choice; the client's duty to echo one
            # is conditional on it arriving. Not minting is sessionless mode.
            new_session: str = await acall(store.create, principal_id=principal)
            http_response[_SESSION_HEADER] = new_session
        return http_response

    async def _handle_modern(
        self, http_request: Any, message: Any, metadata: RequestMetadata
    ) -> HttpResponseBase:
        """Async sibling of the sync view's modern path — same rules.

        A full parallel implementation rather than a wrapper, as everywhere
        else in this transport: threading a sync/async switch through would put
        the era branch somewhere it does not belong.
        """
        refused: JsonResponse | None = modern_header_refusal(
            http_request, message, metadata, self._require_config()
        )
        if refused is not None:
            return refused

        token = await self._authenticate(http_request)
        if token is None:
            return unauthenticated_response(self._require_auth_backend())

        context: MCPCallContext = self._context(
            http_request,
            token,
            protocol_version=metadata.protocol_version,
            session_id=None,
            client_capabilities=metadata.client_capabilities,
        )

        if isinstance(message, JsonRpcNotification):
            return HttpResponse(status=202)

        # Necessarily a request by now. The era test reads ``params``, which a
        # JSON-RPC *response* does not carry, so a response body is always
        # routed to the legacy path — and rejected there.
        #
        # Everything that runs consumer code for this request sits inside the
        # backstop: the subscription grant and the stream's permission
        # pre-flight ask the same scope gates and PermissionChecks a dispatch
        # does.
        # Neither has committed a status when it raises, so a JSON-RPC answer
        # is still possible; once a stream is open, the stream's own error
        # frame takes over.
        try:
            subscribed: HttpResponseBase | None = await self._maybe_subscribe(message, context)
            if subscribed is not None:
                return subscribed
            streamed: HttpResponseBase | None = await self._maybe_stream(message, context)
            if streamed is not None:
                return streamed
            result: Any = await adispatch(message.method, params_dict(message.params), context)
        except Exception as exc:  # noqa: BLE001 — the backstop; see ``internal_error_response``
            if not is_raised_denial(exc):
                return internal_error_response(message, logger)
            # A denial raised rather than returned; see ``raised_denial_error``.
            result = raised_denial_error()
        return modern_result_response(message.id, result, self._require_auth_backend())

    async def get(self, http_request: HttpRequest) -> HttpResponseBase:
        """GET action: open a server-pushed SSE stream for the current session.

        - 405 if no broker is configured, or sessions are off (nothing to
          push, and no channel to push it down). Decided **before**
          authentication: no credential changes this answer, so challenging
          for one would send the client after a token it cannot use.
        - 401 if the caller cannot authenticate — the stream carries a
          session's payloads, so it is gated exactly like POST.
        - 400 if the protocol-version header is missing or unsupported. The
          spec is silent on GET here; this is parity with POST.
        - 404 if the session id is unknown **or owned by a different
          principal** (indistinguishable on purpose).
        - 503 if this worker is already serving
          ``MAX_CONCURRENT_SSE_STREAMS`` streams.
        - Otherwise ``text/event-stream``, with idle keep-alives and whatever
          ``MCPServer.notify`` enqueues, for at most
          ``SSE_STREAM_MAX_SECONDS``.

        **Both bounds exist because this stream is the cheapest thing an
        authenticated caller can hold.** Minting sessions is uncapped and each
        one addresses its own stream, so a caller looping ``initialize`` and
        then ``GET`` parks an ASGI task per session, each keep-aliving itself
        past any proxy's idle timeout. ``subscriptions/listen`` is bounded on
        exactly these two axes for exactly this reason.
        """
        refused: JsonResponse | None = origin_refusal(http_request, self._require_config())
        if refused is not None:
            return refused
        if modern_era_requested(http_request):
            return self._method_not_allowed()

        broker = self.sse_broker
        if broker is None or not self._sessions_enabled():
            # The session id *is* the channel address, so sessionless leaves
            # nothing to open a per-client stream against. 405 is the spec's
            # status for "this endpoint offers no SSE stream".
            #
            # Ahead of authentication, because no credential moves this
            # configuration off 405 and a 401 here carries a challenge that
            # asks the client to go get one. Method support is a fact about
            # the resource, not about the caller — the same reasoning
            # ``_handle_modern`` applies to era detection.
            return self._method_not_allowed()

        token = await self._authenticate(http_request)
        if token is None:
            return unauthenticated_response(self._require_auth_backend())

        config: MCPConfig = self._require_config()
        if (
            negotiate_protocol_version(
                http_request.headers.get(VERSION_HEADER), is_sessionless=False, config=config
            )
            is None
        ):
            return unsupported_version_response()

        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        store = self._require_session_store()
        if not session_id or await acall(store.owner, session_id) != principal_for_token(token):
            return unknown_session_response()

        cap: int | None = config.max_concurrent_sse_streams
        if cap is not None and broker.active_streams >= cap:
            return _capacity_refusal(cap, "SSE streams", "MAX_CONCURRENT_SSE_STREAMS")

        # ``Last-Event-ID`` is honoured only with a replay buffer wired in;
        # otherwise there is nothing buffered to replay and it is ignored.
        last_event_id: str | None = (
            http_request.headers.get("Last-Event-ID")
            if self.sse_replay_buffer is not None
            else None
        )
        return build_sse_response(
            broker,
            session_id,
            replay_buffer=self.sse_replay_buffer,
            last_event_id=last_event_id,
            max_seconds=config.sse_stream_max_seconds,
        )

    async def delete(self, http_request: HttpRequest) -> HttpResponse:
        """DELETE action: end the session named by ``MCP-Session-Id``.

        Requires authentication, and only the principal a session was
        minted for can destroy it — a wrong-principal (or unknown) id
        renders 404 without touching the session.
        """
        refused: JsonResponse | None = origin_refusal(http_request, self._require_config())
        if refused is not None:
            return refused
        if modern_era_requested(http_request) or not self._sessions_enabled():
            # No sessions to terminate — the spec's blessed answer for a server
            # that does not let clients terminate sessions.
            return self._method_not_allowed()
        token = await self._authenticate(http_request)
        if token is None:
            return unauthenticated_response(self._require_auth_backend())
        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        if session_id:
            store = self._require_session_store()
            if await acall(store.owner, session_id) != principal_for_token(token):
                return unknown_session_response()
            # The session is going away, so buffered events would never be
            # delivered.
            if self.sse_replay_buffer is not None:
                await self.sse_replay_buffer.forget(session_id)
            await acall(store.destroy, session_id)
        return HttpResponse(status=204)

    async def _maybe_subscribe(
        self, message: Any, context: MCPCallContext
    ) -> HttpResponseBase | None:
        """Answer ``subscriptions/listen`` with a stream that stays open.

        ``None`` for every other method. Separate from ``_maybe_stream``: that
        one wraps a dispatch and ends with its result, while this has no
        dispatch and ends when the client leaves.

        **Modern era only.** A legacy client reaching here gets dispatch's
        unknown-method answer rather than a stream it cannot interpret.
        """
        if getattr(message, "method", None) != SUBSCRIPTIONS_LISTEN_METHOD:
            return None
        broker = context.subscriptions
        if broker is None:
            # Checked before granting: evaluating permissions and reading the
            # task store to build a grant we then discard is work for nothing.
            # An empty grant closes after the acknowledgement.
            return build_subscription_stream(
                broker=InMemorySubscriptionBroker(),
                topics=frozenset(),
                granted=SubscriptionFilter(),
                request_id=message.id,
                max_seconds=context.config.subscription_max_seconds,
            )

        cap: int | None = context.config.max_concurrent_subscriptions
        if cap is not None and broker.active_subscriptions >= cap:
            return _capacity_refusal(
                cap, "subscriptions", "MAX_CONCURRENT_SUBSCRIPTIONS", request_id=message.id
            )

        params: dict[str, Any] | None = params_dict(message.params)
        requested = SubscriptionFilter.from_params(
            params.get("notifications") if params is not None else None
        )
        # The spec's one exception to "refused entries are dropped": a client
        # asking for task notifications without declaring the tasks extension
        # MUST get an error, not a quiet omission. Answered here rather than in
        # the grant because it produces an error response instead of a stream.
        if requested.task_ids and not declares_tasks_extension(context):
            return modern_refusal(missing_capability_error(), message.id)
        granted, topics = await acall(grant_subscription, requested, context)
        return build_subscription_stream(
            broker=broker,
            topics=topics,
            granted=granted,
            request_id=message.id,
            max_seconds=context.config.subscription_max_seconds,
        )

    async def _maybe_stream(self, message: Any, context: MCPCallContext) -> HttpResponseBase | None:
        """Answer with an SSE stream when the client asked to hear about progress.

        ``None`` means "not this request" — no ``progressToken``, so a single
        JSON object it is. The spec lets the server choose per request, and a
        stream whose only event is the final response costs a connection and
        buys nothing.

        Era-independent: the token sits at ``_meta.progressToken`` in both.
        """
        params: dict[str, Any] | None = params_dict(message.params)
        token: str | int | None = progress_token(params)
        if token is None:
            return None

        # Asking is not enough — the dispatch has to be able to answer. See
        # ``can_report_progress`` for which paths qualify and why.
        if not await acall(can_report_progress, message.method, params, context):
            return None

        # Before the stream opens, while a status can still be chosen. See
        # ``preflight_permissions`` for why this one check moves out here.
        denied: JsonRpcError | None = await acall(
            preflight_permissions, message.method, params, context
        )
        if denied is not None:
            return challenged_error_response(
                denied, request_id=message.id, status=403, backend=self._require_auth_backend()
            )

        def dispatch_with(reporter: ProgressReporter) -> Any:
            return adispatch(
                message.method, params, dataclasses.replace(context, progress=reporter)
            )

        return stream_with_progress(
            dispatch=dispatch_with, request_id=message.id, token=token, context=context
        )

    def _context(
        self,
        http_request: HttpRequest,
        token: Any,
        *,
        protocol_version: str,
        session_id: str | None,
        client_capabilities: dict[str, Any] | None = None,
    ) -> MCPCallContext:
        """The context a dispatch reads, from this view's collaborators.

        ``client_capabilities`` is the modern path's alone: the spec forbids
        relying on a declaration that did not arrive *with the request*, so
        the legacy context leaves it empty.
        """
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
        )

    # ----- collaborator accessors -----

    def _method_not_allowed(self) -> HttpResponse:
        """A ``405`` naming the methods this view routes, as RFC 9110 requires."""
        return HttpResponseNotAllowed(allowed_methods(self))

    async def _authenticate(self, http_request: HttpRequest) -> Any:
        """Resolve the caller's token; on success the request is the token's.

        As the sync view's: the backend sees an anonymous request, and an
        authenticated one carries the token's principal from here on, ahead of
        every context, stream pre-flight and subscription grant built from it.
        """
        backend = self._require_auth_backend()
        seat_anonymous(http_request)
        token: Any = await acall(backend.authenticate, http_request)
        if token is not None:
            carry_principal(http_request, token)
        return token

    def _require_tools(self) -> ToolRegistry:
        if self.tools is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpView is missing a ToolRegistry")
        return self.tools

    def _require_resources(self) -> ResourceRegistry:
        if self.resources is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpView is missing a ResourceRegistry")
        return self.resources

    def _require_prompts(self) -> PromptRegistry:
        if self.prompts is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpView is missing a PromptRegistry")
        return self.prompts

    def _require_auth_backend(self) -> MCPAuthBackend:
        if self.auth_backend is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpView is missing an MCPAuthBackend")
        return self.auth_backend

    def _require_config(self) -> MCPConfig:
        if self.config is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpView is missing an MCPConfig")
        return self.config

    def _sessions_enabled(self) -> bool:
        """Whether the legacy era mints and requires an ``Mcp-Session-Id``."""
        return self._require_config().sessions_enabled

    def _require_session_store(self) -> SessionStore:
        if self.session_store is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpView is missing a SessionStore")
        return self.session_store


__all__ = ["AsyncStreamableHttpView"]
