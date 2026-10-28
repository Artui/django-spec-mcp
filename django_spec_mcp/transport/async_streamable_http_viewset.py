from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping
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
from django_spec_mcp.observability import get_logger, session_fingerprint
from django_spec_mcp.protocol.parse_message import parse_message
from django_spec_mcp.protocol.types.implementation import Implementation
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.json_rpc_notification import JsonRpcNotification
from django_spec_mcp.protocol.types.json_rpc_request import JsonRpcRequest
from django_spec_mcp.protocol.types.json_rpc_response import JsonRpcResponse
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
from django_spec_mcp.transport.origin_validation import is_origin_allowed
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
    exempt_endpoint,
    insufficient_scope_challenge,
    is_permission_denial,
    is_raised_denial,
    modern_error_status,
    not_acceptable,
    raised_denial_error,
    route_actions,
    seat_anonymous,
    session_gate_failure,
)
from django_spec_mcp.transport.validate_modern_request import validate_modern_request
from django_spec_mcp.types.internal_error_message import INTERNAL_ERROR_MESSAGE
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.mcp_error_header import MCP_ERROR_HEADER
from django_spec_mcp.types.modern_protocol_versions import MODERN_PROTOCOL_VERSIONS
from django_spec_mcp.types.sessionless_methods import SESSIONLESS_METHODS
from django_spec_mcp.types.subscriptions_listen_method import SUBSCRIPTIONS_LISTEN_METHOD

_SESSION_HEADER: str = "Mcp-Session-Id"
_VERSION_HEADER: str = "Mcp-Protocol-Version"
# JSON, and the server-sent events a GET, a subscription or a streamed call
# answers with. ``text/event-stream`` alone is the ``Accept`` the spec tells a
# client to send for the GET stream, so it is served rather than refused
# (``test_an_accept_naming_only_a_stream_reaches_the_async_endpoint``).
_ANSWERS_WITH: tuple[str, ...] = ("application/json", "text/event-stream")

# Passed to ``as_view`` by ``MCPServer``, so the canonical routing lives next to
# the view rather than in the URL conf.
ASYNC_STREAMABLE_HTTP_ACTION_MAP: dict[str, str] = {
    "post": "handle_jsonrpc",
    "get": "handle_get",
    "delete": "terminate_session",
}


logger = get_logger(__name__)


def _error_response(
    *,
    code: int,
    message: str,
    data: Any = None,
    status: int = 400,
    request_id: Any = None,
    error_hint: str | None = None,
) -> JsonResponse:
    body: dict[str, Any] = JsonRpcResponse(
        id=request_id, error=JsonRpcError(code=code, message=message, data=data)
    ).to_dict()
    response = JsonResponse(body, status=status)
    if error_hint is not None:
        # Summarises the body for clients that surface only the status line.
        response[MCP_ERROR_HEADER] = error_hint
    return response


def _internal_error_response(message: JsonRpcRequest) -> JsonResponse:
    """Answer a dispatch that raised with a JSON-RPC ``-32603``, and log why.

    The same backstop as the sync sibling's, where the reasoning lives: a
    ``500`` so monitoring still counts a server fault, a generic message, and
    the exception in the log. Must be called from inside the ``except`` block,
    because ``logger.exception`` reads the exception being handled.
    """
    logger.exception(
        "Unhandled exception dispatching %s (request id %r)", message.method, message.id
    )
    return _error_response(
        code=JsonRpcErrorCode.INTERNAL_ERROR,
        message=INTERNAL_ERROR_MESSAGE,
        status=500,
        request_id=message.id,
    )


class AsyncStreamableHttpViewSet(View):
    """Async sibling of ``StreamableHttpViewSet`` for ASGI deployments.

    A plain Django view whose actions are all ``async def``, so Django awaits it
    with no thread hop. Wire behaviour matches the sync view for POST and DELETE — same headers, same
    status codes, same JSON-RPC shapes — and the reasoning behind the shared checks
    lives there. The async path additionally serves server-initiated SSE on GET when an
    [`SSEBroker`][django_spec_mcp.transport.types.sse_broker.SSEBroker] is wired in;
    without one, GET returns 405, which the spec allows when there is nothing to push.

    Sync collaborators (auth backend, session store, permissions) are bridged
    through ``asgiref.sync.sync_to_async``, so a fully sync stack still
    works under ASGI.
    """

    actions: Mapping[str, str] = ASYNC_STREAMABLE_HTTP_ACTION_MAP
    # Declared rather than computed: Django derives it from the class's own
    # ``get`` / ``post`` / ... methods, and these are bound per instance from
    # ``actions``, so the class has none for it to read. Every action is
    # ``async def``, and with this set Django's ``options`` and ``405`` answer
    # as coroutines too.
    view_is_async: bool = True

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
    def as_view(
        cls, actions: Mapping[str, str] | None = None, **initkwargs: Any
    ) -> Callable[..., Any]:
        """Build the view function routing ``actions``, the canonical map if ``None``.

        Django marks the function a coroutine function because
        ``view_is_async`` is set, so the handler awaits it.
        """
        routed: Mapping[str, str] = cls.actions if actions is None else actions
        view = super().as_view(actions=routed, **initkwargs)
        return exempt_endpoint(view, routed, cls)

    def setup(self, request: HttpRequest, *args: Any, **kwargs: Any) -> None:
        route_actions(self, self.actions)
        super().setup(request, *args, **kwargs)

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

    # ----- actions (routed by ``as_view``'s map) -----

    async def handle_jsonrpc(self, http_request: HttpRequest) -> HttpResponseBase:
        guard: HttpResponse | None = self._check_origin(http_request)
        if guard is not None:
            return guard

        max_bytes: int = self._require_config().max_request_bytes
        if len(http_request.body) > max_bytes:
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Request body too large",
                status=413,
            )

        # Wider than ``JSONDecodeError`` for the reasons the sync sibling gives:
        # a ``RecursionError``, an over-long integer's ``ValueError`` and a
        # ``UnicodeDecodeError`` each escaped as a 500, before authentication.
        # Held by ``test_async_body_json_cannot_decode_is_a_parse_error``: its
        # nested case fails without ``RecursionError``, the other two without
        # widening ``JSONDecodeError`` to ``ValueError``.
        try:
            payload: Any = json.loads(http_request.body or b"null")
        except (ValueError, RecursionError) as exc:
            # Only ``JSONDecodeError`` carries ``msg``; the others get a fixed
            # message rather than an interpreter internal. The kept detail is
            # held by ``test_async_decode_error_keeps_the_decoders_detail``.
            detail: str = (
                exc.msg if isinstance(exc, json.JSONDecodeError) else "body could not be decoded"
            )
            return _error_response(
                code=JsonRpcErrorCode.PARSE_ERROR, message=f"Invalid JSON: {detail}"
            )

        try:
            message = parse_message(payload)
        except ValueError as exc:
            return _error_response(code=JsonRpcErrorCode.INVALID_REQUEST, message=str(exc))

        is_initialize: bool = isinstance(message, JsonRpcRequest) and message.method == "initialize"
        # Answerable without a session, but does *not* mint one — hence a
        # second flag rather than a widening of the first.
        is_sessionless: bool = (
            isinstance(message, JsonRpcRequest) and message.method in SESSIONLESS_METHODS
        )

        # **The era fork** — see the sync sibling for the reasoning.
        metadata: RequestMetadata | None = RequestMetadata.from_params(
            _params_dict(getattr(message, "params", None))
        )
        if metadata is not None:
            return await self._handle_modern(http_request, message, metadata)

        version_header: str | None = http_request.headers.get(_VERSION_HEADER)
        negotiated: str | None = negotiate_protocol_version(
            version_header, is_sessionless=is_sessionless, config=self._require_config()
        )
        if negotiated is None:
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Missing or unsupported MCP-Protocol-Version",
                request_id=getattr(message, "id", None),
            )
        protocol_version: str = negotiated

        # Before the session lookup, so session ids cannot be probed through a
        # 404-vs-401 oracle. See the sync sibling.
        token = await self._authenticate(http_request)
        if token is None:
            logger.warning("Authentication failed for %s", http_request.path)
            return self._unauthenticated_response()

        # A wrong-principal presentation renders the same 404 as an unknown id,
        # so the gate is not an ownership oracle.
        store = self._require_session_store()
        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        principal: str = principal_for_token(token)
        if self._sessions_enabled() and not is_sessionless:
            owner_matches = bool(session_id) and await acall(store.owner, session_id) == principal
            failure = session_gate_failure(session_id, owner_matches=owner_matches)
            if failure is not None:
                message_text, status, hint = failure
                # The log names the exact condition the response merges.
                logger.warning(
                    "Session rejected: %s (session=%s, principal=%s, method=%s) -> HTTP %s",
                    hint,
                    session_fingerprint(session_id),
                    principal,
                    getattr(message, "method", "?"),
                    status,
                )
                return _error_response(
                    code=JsonRpcErrorCode.INVALID_REQUEST,
                    message=message_text,
                    status=status,
                    request_id=getattr(message, "id", None),
                    error_hint=hint,
                )

        context = MCPCallContext(
            http_request=http_request,
            token=token,
            tools=self._require_tools(),
            resources=self._require_resources(),
            prompts=self._require_prompts(),
            protocol_version=protocol_version,
            session_id=session_id,
            server_info=self.server_info,
            instructions=self.instructions,
            tasks=self.task_store,
            task_executor=self.task_executor,
            subscriptions=self.subscription_broker,
            config=self._require_config(),
            pool_seeds=self.pool_seeds,
            conventions=self.conventions,
        )

        if isinstance(message, JsonRpcNotification):
            return HttpResponse(status=202)

        if not isinstance(message, JsonRpcRequest):
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST, message="Expected a JSON-RPC request"
            )

        # The stream decision sits inside the backstop with the dispatch: its
        # permission pre-flight runs the same consumer permission classes the
        # dispatch would, so a raise there is the same server fault, and it
        # happens before any stream has committed a status.
        try:
            streamed: HttpResponseBase | None = await self._maybe_stream(message, context)
            if streamed is not None:
                return streamed
            result: Any = await adispatch(message.method, _params_dict(message.params), context)
        except Exception as exc:  # noqa: BLE001 — the backstop; see ``_internal_error_response``
            if not is_raised_denial(exc):
                return _internal_error_response(message)
            # A denial raised rather than returned; see ``raised_denial_error``.
            result = raised_denial_error()

        if isinstance(result, JsonRpcError):
            response_body = JsonRpcResponse(id=message.id, error=result).to_dict()
        else:
            response_body = JsonRpcResponse(id=message.id, result=result).to_dict()
        # 403 plus a challenge naming the missing scopes: the MCP authorization
        # spec's error table makes the status normative. Every other outcome,
        # including a tool that failed on its own terms, stays a 200.
        denied: bool = is_permission_denial(result)
        http_response = JsonResponse(response_body, status=403 if denied else 200)
        if denied:
            http_response["WWW-Authenticate"] = insufficient_scope_challenge(
                result, self._require_auth_backend()
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
        """Async sibling of the sync viewset's modern path — same rules.

        A full parallel implementation rather than a wrapper, as everywhere
        else in this transport: threading a sync/async switch through would put
        the era branch somewhere it does not belong.
        """
        config: MCPConfig = self._require_config()
        request_id: Any = getattr(message, "id", None)
        if isinstance(message, JsonRpcRequest):
            invalid: JsonRpcError | None = validate_modern_request(
                method=message.method,
                params=_params_dict(message.params),
                metadata=metadata,
                headers=http_request.headers,
                supported_versions=config.modern_protocol_versions,
            )
            if invalid is not None:
                return _error_response(
                    code=invalid.code,
                    message=invalid.message,
                    data=invalid.data,
                    status=modern_error_status(invalid),
                    request_id=request_id,
                )

        token = await self._authenticate(http_request)
        if token is None:
            return self._unauthenticated_response()

        context = MCPCallContext(
            http_request=http_request,
            token=token,
            tools=self._require_tools(),
            resources=self._require_resources(),
            prompts=self._require_prompts(),
            protocol_version=metadata.protocol_version,
            session_id=None,
            # Modern path only: the spec forbids relying on a declaration that
            # did not arrive *with the request*, so the legacy context leaves
            # this empty.
            client_capabilities=metadata.client_capabilities,
            server_info=self.server_info,
            instructions=self.instructions,
            tasks=self.task_store,
            task_executor=self.task_executor,
            subscriptions=self.subscription_broker,
            config=config,
            pool_seeds=self.pool_seeds,
            conventions=self.conventions,
        )

        if isinstance(message, JsonRpcNotification):
            return HttpResponse(status=202)

        # Necessarily a request by now. The era test reads ``params``, which a
        # JSON-RPC *response* does not carry, so a response body is always
        # routed to the legacy path — and rejected there.
        #
        # Everything that runs consumer code for this request sits inside the
        # backstop: the subscription grant and the stream's permission
        # pre-flight evaluate the same permission classes a dispatch does.
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
            result: Any = await adispatch(message.method, _params_dict(message.params), context)
        except Exception as exc:  # noqa: BLE001 — the backstop; see ``_internal_error_response``
            if not is_raised_denial(exc):
                return _internal_error_response(message)
            # A denial raised rather than returned; see ``raised_denial_error``.
            result = raised_denial_error()
        if isinstance(result, JsonRpcError):
            body = JsonRpcResponse(id=message.id, error=result).to_dict()
            status: int = modern_error_status(result)
            response = JsonResponse(body, status=status)
            if status == 403:
                response["WWW-Authenticate"] = insufficient_scope_challenge(
                    result, self._require_auth_backend()
                )
            return response
        return JsonResponse(JsonRpcResponse(id=message.id, result=result).to_dict())

    async def handle_get(self, http_request: HttpRequest) -> HttpResponseBase:
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
        guard: HttpResponse | None = self._check_origin(http_request)
        if guard is not None:
            return guard
        if self._modern_era_requested(http_request):
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
            return self._unauthenticated_response()

        version_header: str | None = http_request.headers.get(_VERSION_HEADER)
        if (
            negotiate_protocol_version(
                version_header, is_sessionless=False, config=self._require_config()
            )
            is None
        ):
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Missing or unsupported MCP-Protocol-Version",
            )

        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        store = self._require_session_store()
        if not session_id or await acall(store.owner, session_id) != principal_for_token(token):
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Unknown or missing MCP-Session-Id",
                status=404,
            )

        config: MCPConfig = self._require_config()
        cap: int | None = config.max_concurrent_sse_streams
        if cap is not None and broker.active_streams >= cap:
            # Refused rather than queued, as a subscription past its own cap
            # is: accepting trades a clear error for a worker that stops
            # answering anything at all.
            return _error_response(
                code=JsonRpcErrorCode.INTERNAL_ERROR,
                message=(
                    f"This server is already serving its maximum of {cap} concurrent "
                    "SSE streams. Retry shortly, or raise "
                    "SPEC_MCP['MAX_CONCURRENT_SSE_STREAMS']."
                ),
                status=503,
            )

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

    async def terminate_session(self, http_request: HttpRequest) -> HttpResponse:
        """DELETE action: end the session named by ``MCP-Session-Id``.

        Requires authentication, and only the principal a session was
        minted for can destroy it — a wrong-principal (or unknown) id
        renders 404 without touching the session.
        """
        guard: HttpResponse | None = self._check_origin(http_request)
        if guard is not None:
            return guard
        if self._modern_era_requested(http_request) or not self._sessions_enabled():
            # No sessions to terminate — the spec's blessed answer for a server
            # that does not let clients terminate sessions.
            return self._method_not_allowed()
        token = await self._authenticate(http_request)
        if token is None:
            return self._unauthenticated_response()
        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        if session_id:
            store = self._require_session_store()
            if await acall(store.owner, session_id) != principal_for_token(token):
                return _error_response(
                    code=JsonRpcErrorCode.INVALID_REQUEST,
                    message="Unknown or missing MCP-Session-Id",
                    status=404,
                )
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
            # Refused rather than queued. Each subscription parks a worker for
            # its lifetime, so accepting past the cap trades a clear error for a
            # server that stops answering anything.
            return _error_response(
                code=JsonRpcErrorCode.INTERNAL_ERROR,
                message=(
                    f"This server is already serving its maximum of {cap} concurrent "
                    "subscriptions. Retry shortly, or raise "
                    "SPEC_MCP['MAX_CONCURRENT_SUBSCRIPTIONS']."
                ),
                status=503,
                request_id=message.id,
            )

        params: dict[str, Any] | None = _params_dict(message.params)
        requested = SubscriptionFilter.from_params(
            params.get("notifications") if params is not None else None
        )
        # The spec's one exception to "refused entries are dropped": a client
        # asking for task notifications without declaring the tasks extension
        # MUST get an error, not a quiet omission. Answered here rather than in
        # the grant because it produces an error response instead of a stream.
        if requested.task_ids and not declares_tasks_extension(context):
            denied = missing_capability_error()
            return _error_response(
                code=denied.code,
                message=denied.message,
                data=denied.data,
                status=modern_error_status(denied),
                request_id=message.id,
            )
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
        params: dict[str, Any] | None = _params_dict(message.params)
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
            response = _error_response(
                code=denied.code,
                message=denied.message,
                data=denied.data,
                status=403,
                request_id=message.id,
            )
            response["WWW-Authenticate"] = insufficient_scope_challenge(
                denied, self._require_auth_backend()
            )
            return response

        def dispatch_with(reporter: ProgressReporter) -> Any:
            return adispatch(
                message.method, params, dataclasses.replace(context, progress=reporter)
            )

        return stream_with_progress(
            dispatch=dispatch_with, request_id=message.id, token=token, context=context
        )

    def _modern_era_requested(self, http_request: Any) -> bool:
        """Whether the caller named a modern revision in its version header.

        GET and DELETE carry no body, so the per-request ``_meta`` that decides
        the era elsewhere is unavailable and the header is the only signal.
        ``2026-07-28`` removed both the GET stream and session termination, so
        a caller naming that revision and using either verb gets ``405``.
        """
        version: str | None = http_request.headers.get(_VERSION_HEADER)
        return version in MODERN_PROTOCOL_VERSIONS

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

    def _unauthenticated_response(self) -> JsonResponse:
        challenge: str = self._require_auth_backend().www_authenticate_challenge(
            error="invalid_token"
        )
        response = JsonResponse(
            {"error": "unauthorized", "error_description": "Authentication required."},
            status=401,
        )
        response["WWW-Authenticate"] = challenge
        return response

    def _check_origin(self, request: Any) -> HttpResponse | None:
        origin: str | None = request.headers.get("Origin")
        if not is_origin_allowed(origin, self._require_config().allowed_origins):
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message=f"Origin not allowed: {origin!r}",
                status=403,
            )
        return None

    def _require_tools(self) -> ToolRegistry:
        if self.tools is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpViewSet is missing a ToolRegistry")
        return self.tools

    def _require_resources(self) -> ResourceRegistry:
        if self.resources is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpViewSet is missing a ResourceRegistry")
        return self.resources

    def _require_prompts(self) -> PromptRegistry:
        if self.prompts is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpViewSet is missing a PromptRegistry")
        return self.prompts

    def _require_auth_backend(self) -> MCPAuthBackend:
        if self.auth_backend is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpViewSet is missing an MCPAuthBackend")
        return self.auth_backend

    def _require_config(self) -> MCPConfig:
        if self.config is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpViewSet is missing an MCPConfig")
        return self.config

    def _sessions_enabled(self) -> bool:
        """Whether the legacy era mints and requires an ``Mcp-Session-Id``."""
        return self._require_config().sessions_enabled

    def _require_session_store(self) -> SessionStore:
        if self.session_store is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpViewSet is missing a SessionStore")
        return self.session_store


def _params_dict(params: Any) -> dict[str, Any] | None:
    if params is None:
        return None
    if isinstance(params, dict):
        return params
    return None  # JSON-RPC list params are not used by MCP today.


__all__ = ["ASYNC_STREAMABLE_HTTP_ACTION_MAP", "AsyncStreamableHttpViewSet"]
