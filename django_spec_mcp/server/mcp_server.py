from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any, cast

from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.urls import URLPattern, path
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    RESERVED_POOL_SEEDS,
    UNSET,
    Affordance,
    Parameters,
    PermissionCheck,
    PoolSeeds,
    SelectorKind,
    ServerArguments,
    SpecRegistry,
    UnknownArguments,
    UnsetType,
    check_seeds,
    server_owned_keys,
)
from django_service_specs import SelectorSpec as KernelSelectorSpec
from django_service_specs import ServiceSpec as KernelServiceSpec

from django_spec_mcp.adapters.check_kernel_spec_guarded import check_kernel_spec_guarded
from django_spec_mcp.adapters.kernel_selector_to_resource import kernel_selector_to_resource
from django_spec_mcp.adapters.kernel_spec_to_tool import kernel_spec_to_tool
from django_spec_mcp.adapters.ui_to_resource import ui_view_to_resource
from django_spec_mcp.adapters.utils import merge_meta
from django_spec_mcp.auth.backends.django_oauth_toolkit_backend import (
    DjangoOAuthToolkitBackend,
)
from django_spec_mcp.auth.protected_resource_metadata import ProtectedResourceMetadataViewSet
from django_spec_mcp.auth.types.auth_backend import MCPAuthBackend
from django_spec_mcp.auth.types.self_checking import SelfChecking
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.call_kernel_tool import call_kernel_tool
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.handle_tools_list import handle_tools_list
from django_spec_mcp.handlers.kernel_utils import copy_request
from django_spec_mcp.handlers.listable_tool_bindings import listable_tool_bindings
from django_spec_mcp.handlers.offered_tools import (
    declares_operation_conditions,
    unavailable_tools,
)
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import apply_tool_server_arguments
from django_spec_mcp.output.resolve_structured_output import resolve_structured_output
from django_spec_mcp.protocol.build_server_info import build_server_info
from django_spec_mcp.protocol.types.icon import Icon
from django_spec_mcp.protocol.types.implementation import Implementation
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.prompt_argument import PromptArgument
from django_spec_mcp.protocol.types.tool_result import ToolResult
from django_spec_mcp.registry.prompt_registry import PromptRegistry
from django_spec_mcp.registry.resource_registry import ResourceRegistry
from django_spec_mcp.registry.tool_registry import ToolRegistry
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.registry.types.prompt_binding import PromptBinding
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.registry.types.ui_resource_meta import UIResourceMeta
from django_spec_mcp.registry.types.ui_tool_meta import UIToolMeta
from django_spec_mcp.registry.types.utils import binding_checks, scope_names
from django_spec_mcp.schema.types.agent_conventions import AgentConventions
from django_spec_mcp.server.utils import (
    build_ui_tool_meta,
    check_completions_declared,
    check_list_pagination_declared,
    check_tool_description_present,
    check_tool_permissions_declared,
)
from django_spec_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from django_spec_mcp.subscriptions.utils import (
    topic_for_kind,
    topic_for_resource,
)
from django_spec_mcp.tasks.build_worker_token import build_worker_token
from django_spec_mcp.tasks.django_cache_task_store import DjangoCacheTaskStore
from django_spec_mcp.tasks.run_task import run_task as _run_task
from django_spec_mcp.tasks.types.task_executor import TaskExecutor
from django_spec_mcp.tasks.types.task_record import TaskRecord
from django_spec_mcp.tasks.types.task_store import TaskStore
from django_spec_mcp.transport.async_streamable_http_viewset import (
    ASYNC_STREAMABLE_HTTP_ACTION_MAP,
    AsyncStreamableHttpViewSet,
)
from django_spec_mcp.transport.django_cache_session_store import DjangoCacheSessionStore
from django_spec_mcp.transport.in_memory_sse_broker import InMemorySSEBroker
from django_spec_mcp.transport.streamable_http_viewset import (
    STREAMABLE_HTTP_ACTION_MAP,
    StreamableHttpViewSet,
)
from django_spec_mcp.transport.types.session_store import SessionStore
from django_spec_mcp.transport.types.sse_broker import SSEBroker
from django_spec_mcp.transport.types.sse_replay_buffer import SSEReplayBuffer
from django_spec_mcp.types.jsonrpc_version import JSONRPC_VERSION
from django_spec_mcp.types.notification_kind import NotificationKind
from django_spec_mcp.types.output_format import OutputFormat
from django_spec_mcp.types.reserved_post_fetch_keys import RESERVED_POST_FETCH_KEYS
from django_spec_mcp.types.resource_edge_seeds import RESOURCE_EDGE_SEEDS
from django_spec_mcp.types.resource_encoding import ResourceEncoding
from django_spec_mcp.types.resource_updated_method import RESOURCE_UPDATED_METHOD
from django_spec_mcp.types.task_policy import TaskPolicy
from django_spec_mcp.types.tool_content_kind import ToolContentKind
from django_spec_mcp.types.ui_resource_mime_type import UI_RESOURCE_MIME_TYPE


class MCPServer:
    """A pluggable MCP server serving kernel specs.

    The server owns its tool, resource and prompt registries, an auth backend,
    and a session store — all instance state, no module-level singletons. A
    tool or a resource is a ``django_service_specs`` spec, registered by
    passing it in:

        from django_service_specs import SelectorKind, SelectorSpec, ServiceSpec

        server = MCPServer(name="my-app")
        server.register_tool(
            ServiceSpec(service=create_invoice, permissions=[IsBilling()]),
            name="invoices.create",
        )
        server.register_resource(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=get_invoice, permissions=[IsBilling()]
            ),
            name="invoice",
            uri_template="invoices://{pk}",
        )

    A ``SpecRegistry`` registers in one call, ``register_tools``, with the MCP
    knobs per tool in ``overrides``.

    Mount the URLs in your URL conf the ``admin.site.urls`` way — ``.urls`` is a
    namespaced ``(patterns, app_name, namespace)`` triple ``path()`` mounts
    directly (no ``include()``):

        urlpatterns = [path("mcp/", server.urls)]
        # reverse("mcp:endpoint") · reverse("mcp:protected-resource-metadata")

    What every spec on the mount may read -- a tenant, a locale, a clock -- is
    registered once as ``pool_seeds=``, a kernel
    [`PoolSeeds`][django_service_specs.PoolSeeds]:

        server = MCPServer(
            pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda *, user: user.tenant),
        )

    Every spec the server runs receives the seeds (tools over either
    transport, ``call_tool`` / ``acall_tool``, task workers and resource
    reads), every operation condition it asks is asked with them,
    ``tools/list`` availability included, and a registered name is reserved: a
    client argument cannot occupy it, and a spec read or a URI-template
    variable naming it is refused at registration.

    What the server tells a model in its own words -- the handle description and
    line, a paged tool's scope sentence, the missing-argument message -- is
    ``conventions=``, an
    [`AgentConventions`][django_spec_mcp.schema.types.agent_conventions.AgentConventions].
    Change one sentence and keep the rest:

        server = MCPServer(
            conventions=AgentConventions(handle_line="Pass ids to other tools; never show them."),
        )

    The server still decides where each sentence appears; the conventions
    decide what it says, on every route the seeds above reach.
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        version: str | None = None,
        title: str | None = None,
        icons: tuple[Icon, ...] = (),
        website_url: str | None = None,
        description: str | None = None,
        resource_url: str | None = None,
        config: MCPConfig | None = None,
        auth_backend: MCPAuthBackend | None = None,
        session_store: SessionStore | None = None,
        sse_broker: SSEBroker | None = None,
        sse_replay_buffer: SSEReplayBuffer | None = None,
        task_store: TaskStore | None | UnsetType = UNSET,
        task_executor: TaskExecutor | None = None,
        subscription_broker: SubscriptionBroker | None = None,
        pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
        conventions: AgentConventions | None = None,
        url_namespace: str = "mcp",
    ) -> None:
        # Identity is resolved once, here, so the instance is the single source
        # of truth on the wire. Every unset kwarg defers to ``SERVER_INFO`` —
        # including ``icons=()``, which is "unset" and not a way to suppress
        # configured icons.
        self._server_info: Implementation = build_server_info(
            name=name,
            version=version,
            title=title,
            website_url=website_url,
            icons=icons or None,
        )
        self.name: str = self._server_info.name
        self.version: str = self._server_info.version
        self.title: str | None = self._server_info.title
        self.description: str | None = description
        # The scalar settings, snapshotted once and threaded to the transport
        # and every handler, so nothing reads settings on the request path and
        # two servers here can genuinely differ.
        self._config: MCPConfig = config if config is not None else build_mcp_config()
        self._url_namespace: str = url_namespace
        self._tools: ToolRegistry = ToolRegistry(pool_seeds=pool_seeds)
        self._resources: ResourceRegistry = ResourceRegistry()
        self._prompts: PromptRegistry = PromptRegistry()
        # The classes ``copy_request`` makes an in-process call's copy of a
        # handed request from, one per request class, kept here because the
        # package keeps no module-level state.
        self._request_classes: dict[tuple[type, frozenset[str]], type] = {}
        # ``resource_url`` configures the *default* backend; a custom backend
        # owns its own audience policy, so there is nowhere to forward it to.
        # Refusing beats leaving audience enforcement quietly unconfigured.
        if resource_url is not None and auth_backend is not None:
            raise ImproperlyConfigured(
                "Pass resource_url= or auth_backend=, not both — a custom auth "
                "backend owns its own audience binding. Configure it there, e.g. "
                f"auth_backend=DjangoOAuthToolkitBackend(resource_url={resource_url!r})."
            )
        self._auth_backend: MCPAuthBackend = (
            auth_backend
            if auth_backend is not None
            else DjangoOAuthToolkitBackend(resource_url=resource_url)
        )
        # Namespaced by default: the cache-backed store shares one Django cache
        # across every server in the process, so without this a session minted
        # at ``/public/mcp`` satisfies ``/internal/mcp``'s ownership check and a
        # DELETE against either destroys the other's session. Keyed on ``name``,
        # not ``url_namespace``: a server used only in-process is never mounted,
        # so its namespace is a meaningless default that would collide it with a
        # mounted one — and Django's duplicate-namespace check (urls.W005)
        # cannot see an unmounted server.
        self._session_store: SessionStore = (
            session_store
            if session_store is not None
            else DjangoCacheSessionStore(namespace=self.name)
        )
        # Single-process SSE works out of the box; multi-worker ASGI passes a
        # ``RedisSSEBroker`` (or any other ``SSEBroker``) explicitly.
        self._sse_broker: SSEBroker = sse_broker or InMemorySSEBroker()
        # Opt-in: ``None`` means no resume support — no ``id:`` lines,
        # ``Last-Event-ID`` silently ignored.
        self._sse_replay_buffer: SSEReplayBuffer | None = sse_replay_buffer
        # No default broker is constructed. A server that quietly got the
        # in-memory one would advertise subscription support and then deliver
        # nothing as soon as a second worker existed, and the failure looks
        # exactly like "nothing ever changed".
        self._subscription_broker: SubscriptionBroker | None = subscription_broker
        # The project's own always-available names -- a tenant, a locale, a
        # clock -- which over HTTP hang off ``request`` and off it have no
        # channel. Mount-wide, with no per-call or per-tool override: what
        # varies per call belongs in the resolver, which receives ``user`` and
        # ``request`` from the pool, and a mount needing different seeds is a
        # second server. Read by every dispatch and every condition this server
        # asks, and by registration, which reserves the names.
        self._pool_seeds: PoolSeeds = pool_seeds
        # The sentences this server writes for a model. Instance state like the
        # seeds above, and for the same reason: two servers in one project can
        # be talking to different readers. Read into every context this server
        # builds, in-process here and over HTTP by both viewsets.
        self._conventions: AgentConventions = (
            conventions if conventions is not None else AgentConventions()
        )
        # The executor is the switch: supply one and a cache-backed store
        # appears (namespaced like the session store, for the same reason),
        # supply neither and this server runs no tasks. A store with nowhere to
        # run the work would advertise the extension, hand out handles and
        # finish none of them. ``UNSET`` keeps ``task_store=None`` distinct from
        # "not passed" — that is "I will wire the store later", not a request
        # for the default.
        self._task_executor: TaskExecutor | None = task_executor
        self._task_store: TaskStore | None
        if not isinstance(task_store, UnsetType):
            self._task_store = task_store
        elif task_executor is not None:
            self._task_store = DjangoCacheTaskStore(namespace=self.name)
        else:
            self._task_store = None

    # ----- imperative registration -----

    def register_tool(
        self,
        spec: KernelServiceSpec | KernelSelectorSpec,
        *,
        name: str,
        description: str | None = None,
        title: str | None = None,
        display_name: str | None = None,
        display_description: str | None = None,
        icons: tuple[Icon, ...] = (),
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        ui: UIToolMeta | None = None,
        output_format: OutputFormat | str = OutputFormat.JSON,
        content_kind: ToolContentKind = ToolContentKind.TEXT,
        content_mime_type: str | None = None,
        include_structured_content: bool | None = None,
        include_output_schema: bool | None = None,
        task_policy: TaskPolicy = TaskPolicy.FORBIDDEN,
        invalidates: tuple[str, ...] | list[str] = (),
        paginate: bool = False,
        max_page_size: int | None | UnsetType = UNSET,
        max_result_bytes: int | None | UnsetType = UNSET,
        dispatch_timeout: float | None | UnsetType = UNSET,
        always_listed: bool = False,
        rate_limits: list[Any] | None = None,
        scopes: str | Sequence[str] | None = None,
        unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
        server_arguments: ServerArguments | None = None,
    ) -> KernelToolBinding:
        """Register a kernel spec as a tool: a ``ServiceSpec`` or a ``SelectorSpec``.

        ``spec`` is a ``django_service_specs`` spec, and its class says which kind
        of tool this is. A service tool's arguments are the spec's parameters at
        the top level of ``arguments``, as the kernel's ``spec_input_schema``
        advertises them; a selector tool reads, and a ``LIST`` one paginates
        under ``paginate=True``. The call runs through the kernel's ``dispatch``
        / ``adispatch``, which asks the spec's own ``permissions`` of the
        principal the auth backend resolved for the request.

        Anything that is not a kernel spec is refused with a ``TypeError``. A
        knob of the other kind of tool is
        refused: ``paginate`` / ``max_page_size`` on a service tool,
        ``invalidates`` on a selector tool.

        A ``LIST`` selector tool registered without ``paginate=True`` warns
        ``UnboundedListWarning``, and ``REQUIRE_LIST_PAGINATION`` refuses it. A
        paginated one whose spec declares a read named ``page`` or ``limit`` is
        refused, since the pagination takes both names from the arguments
        before the selector runs. A presenter whose audience markings cannot be
        projected (two labels, say) is refused naming the tool, rather than
        failing every ``tools/list`` of the server.

        A declaration no call on this server can run as written is refused
        naming the tool, by the kernel's ``check_seeds`` with the server's
        ``pool_seeds``: a parameter named like a registered seed or the call's
        own ``request`` or ``view``, a positional-only parameter with no
        default, a keyword the callable requires that nothing fills, and an
        optional read over a keyword the callable requires. A read
        ``server_arguments`` binds a value to is filled on every call, so it is
        never refused as optional.

        ``display_name`` and ``display_description`` are labels for in-process
        consumers, read off the binding and never sent on the wire.

        ``scopes`` are the OAuth scopes the caller's token must carry, every one
        of them; a token missing one is answered ``-32006`` with every declared
        scope in ``requiredScopes``, as a prompt's or a view's scope gate
        answers. A bare string is one scope. ``scopes=()``
        is refused, since it would read as a gate and admit every caller. The
        scope gate is a transport check and does not count as the tool's guard
        for ``REQUIRE_TOOL_PERMISSIONS``, which reads the spec's own
        ``permissions``.

        ``task_policy`` means the same for a service tool and a selector tool:
        ``OPTIONAL`` answers a client that declared the tasks extension
        with a task handle and runs inline for one that did not, and
        ``REQUIRED`` refuses the latter with ``-32021``. The spec's own checks
        are asked before the task is created, and the worker runs the call,
        under either policy, through the same ``dispatch`` a direct call takes,
        under the server's ``pool_seeds`` and ``conventions``.

        A refusal from ``dispatch`` is answered on the wire as
        ``kernel_refusals`` words it: ``NotPermitted`` and ``PrincipalUnavailable`` as
        ``-32006``, ``InvalidArguments``, ``ServiceValidationError`` and a bare
        ``DispatchError`` as the ``validation_error`` result, an
        ``AdditionalInputRequired`` as MRTR's ``input_required``, and any other
        ``ServiceError`` as the ``service_error`` result.
        """
        binding = self._kernel_tool_binding(
            spec,
            name=name,
            description=description,
            title=title,
            display_name=display_name,
            display_description=display_description,
            icons=icons,
            annotations=annotations,
            meta=meta,
            ui=ui,
            output_format=output_format,
            content_kind=content_kind,
            content_mime_type=content_mime_type,
            include_structured_content=include_structured_content,
            include_output_schema=include_output_schema,
            task_policy=task_policy,
            invalidates=invalidates,
            paginate=paginate,
            max_page_size=max_page_size,
            max_result_bytes=max_result_bytes,
            dispatch_timeout=dispatch_timeout,
            always_listed=always_listed,
            rate_limits=rate_limits,
            scopes=scopes,
            unknown_arguments=unknown_arguments,
            server_arguments=server_arguments,
        )
        self._tools.register(binding)
        return binding

    def _kernel_tool_binding(
        self,
        spec: KernelServiceSpec | KernelSelectorSpec,
        *,
        name: str,
        description: str | None = None,
        title: str | None = None,
        display_name: str | None = None,
        display_description: str | None = None,
        icons: tuple[Icon, ...] = (),
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        ui: UIToolMeta | None = None,
        output_format: OutputFormat | str = OutputFormat.JSON,
        content_kind: ToolContentKind = ToolContentKind.TEXT,
        content_mime_type: str | None = None,
        include_structured_content: bool | None = None,
        include_output_schema: bool | None = None,
        task_policy: TaskPolicy = TaskPolicy.FORBIDDEN,
        invalidates: tuple[str, ...] | list[str] = (),
        paginate: bool = False,
        max_page_size: int | None | UnsetType = UNSET,
        max_result_bytes: int | None | UnsetType = UNSET,
        dispatch_timeout: float | None | UnsetType = UNSET,
        always_listed: bool = False,
        rate_limits: list[Any] | None = None,
        scopes: str | Sequence[str] | None = None,
        unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
        server_arguments: ServerArguments | None = None,
    ) -> KernelToolBinding:
        """``register_tool`` up to the registration: the binding, built and checked.

        Split out so ``register_tools`` can build and check every entry before it
        registers any.
        """
        ui_meta = build_ui_tool_meta(
            name=name,
            ui=ui,
            meta=meta,
            resources=self._resources,
            include_structured_content=include_structured_content,
            default_structured_content=self._config.include_structured_content,
        )
        binding = kernel_spec_to_tool(
            spec,
            name=name,
            description=description,
            title=title,
            display_name=display_name,
            display_description=display_description,
            icons=icons,
            annotations=annotations,
            meta=merge_meta(ui_meta, meta),
            output_format=OutputFormat.coerce(output_format),
            content_kind=content_kind,
            content_mime_type=content_mime_type,
            include_structured_content=include_structured_content,
            include_output_schema=include_output_schema,
            task_policy=task_policy,
            invalidates=tuple(invalidates),
            paginate=paginate,
            max_page_size=max_page_size,
            max_result_bytes=max_result_bytes,
            dispatch_timeout=dispatch_timeout,
            always_listed=always_listed,
            rate_limits=tuple(rate_limits or ()),
            scopes=scopes,
            unknown_arguments=unknown_arguments,
            server_arguments=server_arguments,
        )
        # Derived now rather than on first read: the listing derives it for
        # every tool, so markings the kernel cannot project (two labels, say)
        # would otherwise register and then take ``tools/list`` down for every
        # tool on the server. The kernel's refusal does not name the tool, so
        # this does
        # (``test_a_presenter_whose_markings_cannot_project_is_refused_at_registration``).
        try:
            _ = binding.audience_projection
        except ImproperlyConfigured as exc:
            raise ImproperlyConfigured(f"MCP tool {binding.name!r}: {exc}") from exc
        # A declaration this server's calls cannot run as written is refused
        # here, rather than listed and failed on every call: a parameter a
        # registered seed or the call's own ``request`` / ``view`` meets, a
        # positional-only one, a keyword nothing fills, and an optional read over
        # a keyword that requires it. The kernel's reader, handed this server's
        # seeds, so the refusal is the one ``dispatch`` would make
        # (``test_a_read_a_seed_occupies_is_refused_at_registration``,
        # ``test_a_required_parameter_nothing_fills_is_refused_at_registration``).
        # Asked of the spec as this server calls it: a read the binding's
        # ``server_arguments`` fill is never left out, so it is judged as
        # demanded (``_as_called``). The binding keeps the spec as declared.
        bound = frozenset(binding.server_arguments.values)
        try:
            check_seeds(_as_called(binding.spec, bound), pool_seeds=self._pool_seeds)
        except ImproperlyConfigured as exc:
            raise ImproperlyConfigured(f"MCP tool {binding.name!r}: {exc}") from exc
        # The spec's own permissions are the tool's guard; the scope gate on
        # ``binding.permissions`` is not one, so it is not what is counted.
        if check_kernel_spec_guarded(
            binding.name, binding.spec, require=self._config.require_tool_permissions
        ):
            # Only this warning earns the spec its per-call grant; a binding
            # passed straight to ``tools.register`` is never marked.
            object.__setattr__(binding, "unguarded", True)
        # ``register_tools`` reaches here through a generator and ``register_tool``
        # directly, so the depth to the caller is counted rather than fixed; the
        # extra level is the check's own frame.
        caller_level = _caller_stacklevel() + 1
        check_tool_description_present(
            binding.name,
            binding.description,
            require=self._config.require_tool_descriptions,
            stacklevel=caller_level,
        )
        resolve_structured_output(
            include_output_schema_override=binding.include_output_schema,
            include_structured_content_override=binding.include_structured_content,
            binding_name=binding.name,
            default_output_schema=self._config.include_output_schema,
            default_structured_content=self._config.include_structured_content,
        )
        # LIST only: a RETRIEVE selector returns one instance and a service has
        # no kind, so neither has a page to bound (``test_retrieve_selector_is_exempt``,
        # ``test_a_service_tool_is_exempt``).
        if binding.kind is SelectorKind.LIST:
            _refuse_pagination_named_reads(binding)
            check_list_pagination_declared(
                binding.name,
                paginate=binding.paginate,
                require=self._config.require_list_pagination,
                stacklevel=caller_level,
            )
        return binding

    def register_tools(
        self,
        registry: SpecRegistry,
        *,
        overrides: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> tuple[KernelToolBinding, ...]:
        """Register every kernel spec in a ``SpecRegistry`` as a tool, in order.

        Each entry registers through ``register_tool`` under its registry name,
        with the knobs ``overrides`` holds under that name, so an entry and a
        direct ``register_tool`` of the same spec are the same tool. An
        ``overrides`` key the registry does not hold raises ``ValueError``, as
        a typo rather than a no-op, and an entry that is not a kernel
        ``ServiceSpec`` or ``SelectorSpec`` (a ``SpecSource`` among them) is
        refused as ``register_tool`` refuses it. A refusal of any entry
        registers none of them: every entry is built and checked first.

        Returns the bindings in registration order.
        """
        override_map = dict(overrides or {})
        unknown = sorted(name for name in override_map if name not in registry)
        if unknown:
            raise ValueError(
                f"overrides name specs not in this SpecRegistry: {unknown}. "
                f"Registered names: {sorted(entry.name for entry in registry.all())}."
            )
        bindings = tuple(
            self._kernel_tool_binding(
                cast(KernelServiceSpec | KernelSelectorSpec, entry.spec),
                name=entry.name,
                **dict(override_map.get(entry.name, {})),
            )
            for entry in registry.all()
        )
        # Every refusal is reached before the first registration, so a refused
        # entry registers nothing
        # (``test_register_tools_registers_nothing_when_an_entry_is_refused``): a
        # scratch registry runs the registry's own checks over the batch (its
        # ``server-arguments`` case fails without it), and a name this server
        # already holds is refused against the live one.
        trial = ToolRegistry(pool_seeds=self._pool_seeds)
        for binding in bindings:
            trial.register(binding)
        taken = sorted(binding.name for binding in bindings if binding.name in self._tools)
        if taken:
            raise ValueError(f"Duplicate MCP tool name(s): {taken}")
        for binding in bindings:
            self._tools.register(binding)
        return bindings

    # ----- transport-neutral invocation -----

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        user: Any,
        request: Any = None,
    ) -> ToolResult:
        """Invoke a registered tool off the HTTP / JSON-RPC path.

        The transport-neutral entry point: hand a tool ``name``, a flat
        ``arguments`` dict and the acting ``user``, and get back the same
        [`ToolResult`][django_spec_mcp.protocol.types.tool_result.ToolResult] the
        wire handlers build. An in-process consumer calls this instead of
        re-implementing dispatch.

        The call runs through the kernel's ``dispatch`` as on the wire: the
        binding's ``server_arguments`` in place, the spec's own ``permissions``
        asked of ``user``, then the spec run and presented, a paged tool's
        pagination included. The transport-level checks are not consulted: the
        ``scopes=`` gate and rate limits read a token, and there is none in
        process. For those, and for tool listing, use ``acall_tool`` /
        ``list_tools``.

        A refusal the wire answers ``-32006`` (``NotPermitted``,
        ``PrincipalUnavailable``) is raised for the caller to map to its own
        wire; every other refusal comes back as the ``isError`` result the wire
        serves.

        Raises ``KeyError`` when no tool is registered under ``name``.
        """
        binding = self._tools.get(name)
        if binding is None:
            raise KeyError(f"No tool registered under {name!r}.")
        return call_kernel_tool(
            binding,
            # The binding's ``server_arguments`` in place before anything reads
            # the arguments, as on the wire.
            apply_tool_server_arguments(binding, arguments or {}),
            user=user,
            request=request,
            config=self._config,
            conventions=self._conventions,
            pool_seeds=self._pool_seeds,
            request_classes=self._request_classes,
        )

    # ----- in-process transport invocation -----

    def list_tools(
        self,
        cursor: str | None = None,
        *,
        user: Any,
        request: Any = None,
        scopes: Sequence[str] | None = None,
        include_unavailable: bool = False,
    ) -> dict[str, Any] | JsonRpcError:
        """List the tools this server exposes, exactly as the wire would.

        The in-process twin of a ``tools/list`` request: one page of the tool catalog
        with the *same* ``inputSchema`` the HTTP transport advertises (the spec's
        parameters, a paginated tool's ``page`` / ``limit`` and the
        ``additionalProperties`` policy), the same per-caller listing-permission filter
        (``FILTER_LISTINGS_BY_PERMISSIONS``) and the same opaque-cursor pagination —
        pass the returned ``nextCursor`` back for the next page. A
        [`JsonRpcError`][django_spec_mcp.protocol.types.json_rpc_error.JsonRpcError]
        signals a bad cursor.

        ``scopes`` are the caller's granted scopes; pass them so a tool
        registered with ``scopes=`` is visible under
        ``FILTER_LISTINGS_BY_PERMISSIONS`` exactly as it would be on the wire.
        A tool whose operation-scope ``affordances`` condition is unmet for
        ``user`` right now is left out, as on the wire, unless
        ``include_unavailable=True``, which lists every tool the caller may see
        and asks no condition. That is for a consumer that builds its tool
        definitions once and asks ``unavailable_tools`` each step: it needs a
        definition for a tool that is unavailable when it lists and becomes
        available later. The wire has no such parameter.

        Unlike ``call_tool`` (the spec core) this is the full transport
        surface. Under an event loop use ``alist_tools`` — a listing
        permission filter or an affordance condition that hits the DB raises
        ``SynchronousOnlyOperation`` from a sync call on the loop.
        """
        params = {"cursor": cursor} if cursor is not None else None
        return handle_tools_list(
            params,
            self._call_context(user=user, request=request, scopes=scopes),
            include_unavailable=include_unavailable,
        )

    async def alist_tools(
        self,
        cursor: str | None = None,
        *,
        user: Any,
        request: Any = None,
        scopes: Sequence[str] | None = None,
        include_unavailable: bool = False,
    ) -> dict[str, Any] | JsonRpcError:
        """Async ``list_tools`` — safe to call from an event loop.

        Listing itself is pure Python, but the per-caller permission filter
        (``FILTER_LISTINGS_BY_PERMISSIONS``) and a tool's operation-scope
        ``affordances`` condition may each run a DB-backed check, which raises
        ``SynchronousOnlyOperation`` when reached synchronously from within an
        event loop. The whole sync handler therefore runs in Django's
        thread-sensitive executor.
        """
        params = {"cursor": cursor} if cursor is not None else None
        context = self._call_context(user=user, request=request, scopes=scopes)
        return await sync_to_async(handle_tools_list, thread_sensitive=True)(
            params, context, include_unavailable=include_unavailable
        )

    def unavailable_tools(
        self,
        *,
        user: Any,
        request: Any = None,
        scopes: Sequence[str] | None = None,
    ) -> dict[str, Affordance]:
        """The tools ``list_tools`` leaves out for this caller right now, and why.

        Maps each omitted tool's name to the operation-scope ``Affordance`` whose
        condition is unmet: its ``code`` and its ``reason``, the sentence written
        for people and models alike. The answer comes from the same pass ``tools/list`` makes, over the
        same tools the caller may see (``FILTER_LISTINGS_BY_PERMISSIONS``
        included), so a name is here exactly when the listing omits it, and a
        tool hidden from this caller is never named with its reason.

        For an in-process consumer that lists once with
        ``include_unavailable=True`` and asks this each step: it offers its model
        what a fresh ``tools/list`` would, and can tell the model why the rest
        are missing, which a listing cannot, since it only leaves them out.
        ``always_listed=True`` tools are never here, as they are never left out.
        Advisory like the listing: ``acall_tool`` still enforces every condition.

        Under an event loop use ``aunavailable_tools``; a condition or listing
        permission that queries raises ``SynchronousOnlyOperation`` from here.
        """
        context = self._call_context(user=user, request=request, scopes=scopes)
        return unavailable_tools(listable_tool_bindings(context), context)

    async def aunavailable_tools(
        self,
        *,
        user: Any,
        request: Any = None,
        scopes: Sequence[str] | None = None,
    ) -> dict[str, Affordance]:
        """Async ``unavailable_tools`` -- safe to call from an event loop.

        Runs in Django's thread-sensitive executor, as ``alist_tools`` does, since
        a condition or a listing permission may query. A server none of whose
        tools declares an operation-scope condition answers ``{}`` without the
        hop: that is decided from the declarations alone, and a consumer asking
        on every model step should not pay a thread switch per step to learn it.
        """
        if not declares_operation_conditions(self._tools.all()):
            return {}
        return await sync_to_async(self.unavailable_tools, thread_sensitive=True)(
            user=user, request=request, scopes=scopes
        )

    async def acall_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        user: Any,
        request: Any = None,
        scopes: Sequence[str] | None = None,
    ) -> dict[str, Any] | JsonRpcError:
        """Invoke a tool off the HTTP path with full transport semantics (async).

        The in-process twin of a ``tools/call`` request: routes through the same async
        handler the wire uses, so the transport-level MCP permissions and rate limits,
        the pagination of a ``LIST`` tool and the output format all
        apply — everything ``call_tool`` omits. Returns the wire's result payload (a
        ``dict`` carrying ``content`` / ``structuredContent`` / ``isError``), or a
        [`JsonRpcError`][django_spec_mcp.protocol.types.json_rpc_error.JsonRpcError]
        for a protocol fault (unknown tool, malformed ``arguments`` shape, denied
        permission).

        ``request`` is the originating Django request when there is one; a
        minimal one is synthesised otherwise, mirroring ``call_tool``.
        ``scopes`` populate the synthetic token so a tool registered with
        ``scopes=`` is invokable in-process just as it is on the wire.
        """
        params = {"name": name, "arguments": arguments or {}}
        return await handle_tools_call_async(
            params, self._call_context(user=user, request=request, scopes=scopes)
        )

    def _call_context(
        self,
        *,
        user: Any,
        request: Any = None,
        scopes: Sequence[str] | None = None,
        session_id: str | None = None,
    ) -> MCPCallContext:
        """Build the per-call context the wire handlers thread through.

        When ``request`` is ``None`` a minimal
        ``HttpRequest`` is synthesised bearing the user, so
        permission classes reading ``request.user`` behave as they would on
        HTTP. A handed request is the caller's own, and the call's user is
        never written to it: the call works on a copy (``copy_request``),
        which the handlers then seat the token's principal on, and which
        shares the handed request's body and parse state, so what a spec
        reads of the body it reads for the caller. The protocol version is
        the server's first (most preferred) supported version, not a
        hardcoded literal.
        """
        http_request: HttpRequest = (
            copy_request(request, self._request_classes) if request is not None else HttpRequest()
        )
        if request is None:
            http_request.user = user
            http_request.method = "POST"
        return MCPCallContext(
            http_request=http_request,
            token=TokenInfo(user=user, scopes=tuple(scopes or ())),
            tools=self._tools,
            resources=self._resources,
            prompts=self._prompts,
            protocol_version=self._config.protocol_versions[0],
            session_id=session_id,
            server_info=self._server_info,
            instructions=self.description,
            config=self._config,
            tasks=self._task_store,
            task_executor=self._task_executor,
            subscriptions=self._subscription_broker,
            pool_seeds=self._pool_seeds,
            conventions=self._conventions,
        )

    def run_task(self, task_id: str) -> None:
        """Execute a queued task. **This is what a worker calls.**

        The other end of ``task_executor.enqueue``, and the whole public
        surface of the worker side:

            @shared_task
            def run_mcp_task(task_id: str) -> None:
                my_server.run_task(task_id)

        Everything it needs comes out of the store: the tool, the arguments,
        and the authorization context to re-check them under. Nothing is
        returned — the client learns the outcome by polling ``tasks/get``.

        Safe to call for an id that is unknown, already claimed or already
        finished: each is a no-op. Queues deliver at least once, and a retried
        delivery must not run a mutation twice.

        Raises when the server has no task store: a worker calling this on a
        server that cannot run tasks would otherwise fail as silence — the job
        "succeeds" and the client polls a handle forever.
        """
        store: TaskStore | None = self._task_store
        if store is None:
            raise ImproperlyConfigured(
                f"MCPServer {self.name!r} has no task store, so run_task() has nothing "
                "to read. Pass task_executor= (which builds a default store) or an "
                "explicit task_store= when constructing the server."
            )
        _run_task(store, task_id, context_factory=self._worker_context)

    def _worker_context(self, record: TaskRecord) -> MCPCallContext:
        """The context a task runs under, off the request path.

        Rebuilt rather than remembered: the identity half comes back out of the
        record (see ``build_worker_token``), the registries, config and stores
        from this server.

        ``client_capabilities`` is empty, since the worker serves no client.
        ``in_task_worker`` is what keeps the run from becoming another task:
        ``maybe_create_task`` lets it through under either policy, so a
        ``REQUIRED`` tool's task runs rather than being refused for a
        declaration no worker makes.
        """
        http_request = HttpRequest()
        http_request.method = "POST"
        token: TokenInfo = build_worker_token(record)
        http_request.user = token.user
        return MCPCallContext(
            http_request=http_request,
            token=token,
            tools=self._tools,
            resources=self._resources,
            prompts=self._prompts,
            protocol_version=self._config.protocol_versions[0],
            server_info=self._server_info,
            instructions=self.description,
            config=self._config,
            tasks=self._task_store,
            task_executor=self._task_executor,
            subscriptions=self._subscription_broker,
            pool_seeds=self._pool_seeds,
            conventions=self._conventions,
            enforce_rate_limits=False,
            in_task_worker=True,
        )

    async def notify_resource_updated(self, uri: str) -> int:
        """Tell every subscriber watching ``uri`` that it changed.

        The explicit trigger, and the one that always works. Call it from
        wherever the write actually happens — a management command, a Celery
        job, a ``save()`` override, a signal handler you wrote yourself:

            await server.notify_resource_updated(f"invoices://{invoice.pk}")

        Returns how many subscribers were reached — a diagnostic, not a
        guarantee: ``0`` means nobody was listening, which is the ordinary case
        and not an error. Notifications are best-effort by design; a client
        that misses one re-reads the resource.

        **A URI, not a template.** Publish the concrete URI that changed, and
        the collection URI too if watchers of the collection should hear about
        it — matching is exact, deliberately (see
        [`topic_for_resource`][django_spec_mcp.subscriptions.utils.topic_for_resource]).

        **Publish after the transaction commits.** Inside
        ``transaction.atomic()`` this announces a change that may still roll
        back, and a subscriber that re-reads immediately sees the old value —
        worse than no notification at all. ``transaction.on_commit`` is the
        Django-native answer.
        """
        return await self._publish(
            topic_for_resource(uri),
            {
                "jsonrpc": JSONRPC_VERSION,
                "method": RESOURCE_UPDATED_METHOD,
                "params": {"uri": uri},
            },
        )

    async def notify_list_changed(self, kind: NotificationKind) -> int:
        """Tell subscribers that one of the catalogs changed.

        Registration happens once at configuration time, so the set of
        registered bindings is fixed for the life of the process. Two things
        still change a catalog under a running client: a server that registers
        tools from data — a plugin loader, a per-tenant catalog — and a tool
        whose operation-scope ``affordances`` condition flips, which
        ``tools/list`` answers on every request and so starts or stops listing
        it. Call this with ``TOOLS_LIST_CHANGED`` after the event that flipped
        the condition, once it has committed. It reaches only clients this
        server can push to: a subscription broker configured and a modern-era
        caller listening, the same two things ``listChanged`` is advertised on.
        """
        return await self._publish(
            topic_for_kind(kind), {"jsonrpc": JSONRPC_VERSION, "method": kind.method}
        )

    async def _publish(self, topic: str, payload: dict[str, Any]) -> int:
        """Hand a notification to the broker, or drop it if there is none.

        A server with no broker simply does not push, so this is a no-op rather
        than an error — which is what keeps ``notify_resource_updated`` safe to
        call unconditionally from a service.
        """
        if self._subscription_broker is None:
            return 0
        return await self._subscription_broker.publish(topic, payload)

    @property
    def subscription_broker(self) -> SubscriptionBroker | None:
        return self._subscription_broker

    @property
    def task_store(self) -> TaskStore | None:
        return self._task_store

    @property
    def task_executor(self) -> TaskExecutor | None:
        return self._task_executor

    def register_resource(
        self,
        spec: KernelSelectorSpec,
        /,
        *,
        name: str,
        uri_template: str,
        description: str | None = None,
        title: str | None = None,
        icons: tuple[Icon, ...] = (),
        mime_type: str = "application/json",
        encoding: ResourceEncoding = ResourceEncoding.JSON,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        always_listed: bool = False,
        cache_ttl_ms: int | UnsetType = UNSET,
        completions: dict[str, Callable[..., Any]] | None = None,
        scopes: str | Sequence[str] | None = None,
    ) -> KernelResourceBinding:
        """Register a kernel ``RETRIEVE`` ``SelectorSpec`` as an MCP resource.

        ``register_resource(spec, uri_template=..., name=...)`` takes a
        ``django_service_specs`` spec positionally. The read runs it through the
        kernel's ``dispatch`` / ``adispatch``, with the URI-template variables
        as its arguments, so the spec's ``reads`` name them and its presenter
        renders the row. Anything that is not a kernel ``RETRIEVE``
        ``SelectorSpec`` is refused with a ``TypeError``. ``scopes`` gate the
        read as they gate ``register_tool``.

        ``meta`` is the generic ``_meta`` bundle for this resource's listing
        entry — ``resources/list`` for a concrete URI,
        ``resources/templates/list`` for a template — and for the ``contents``
        block ``resources/read`` returns.

        ``encoding`` decides how what the presenter returns becomes the read
        body. ``JSON`` pretty-prints it; ``TEXT`` returns it verbatim, and it
        must be a ``str``; ``BLOB`` base64-encodes it into the ``contents``
        entry's ``blob`` instead of ``text``, and it must be ``bytes``,
        ``bytearray`` or ``memoryview``. A value of the wrong type is answered
        at read time as an ``INTERNAL_ERROR`` naming what produced it. Anything
        whose ``mime_type`` is not JSON — Markdown, CSV, plain text — wants
        ``TEXT``, or the document comes back wrapped in a quoted string literal,
        and an image wants ``BLOB``: a presenter rendering the row as Markdown
        pairs with ``encoding=TEXT`` and ``mime_type="text/markdown"``.
        ``mime_type`` is advertised as given and never inferred from or checked
        against the encoding, so ``TEXT`` left at the default
        ``application/json`` serves a ``str`` that is already a JSON document as
        it is. The result ceiling and the cache hints apply to every encoding.
        A ``TEXT`` or ``BLOB`` resource is refused, naming both, beside a spec
        declaring ``affordances``, which the kernel adds to a presented object,
        or ``allow_none``, under which a missing row presents as ``None``
        without the presenter running: neither is a document or bytes. For an
        HTML view use ``register_ui_resource``, which sets both.

        A ``uri_template`` variable is a caller-controlled name that reaches the
        read's arguments, so one named after a pool seed (``user``,
        ``request``, ``data`` …) or declared twice raises here rather than
        letting a URI segment stand in for the authenticated identity. So does
        a variable no read of the spec declares, and one the server fills for
        the call. A spec no read can run as written is refused naming the
        resource, as ``register_tool`` refuses one. A read the URI template
        names is filled on every read, as a value ``server_arguments`` binds is
        on every call, so it is never refused as optional.

        A resource whose spec declares no ``permissions`` is refused for the
        same reason a tool is — see ``REQUIRE_TOOL_PERMISSIONS``. The same
        selector exposed as a resource is as reachable as it is exposed as a
        tool.
        """
        binding = kernel_selector_to_resource(
            spec,
            name=name,
            uri_template=uri_template,
            description=description,
            title=title,
            icons=icons,
            rate_limits=tuple(rate_limits or ()),
            annotations=annotations,
            meta=merge_meta(meta),
            always_listed=always_listed,
            cache_ttl_ms=cache_ttl_ms,
            completions=completions,
            scopes=scopes,
            mime_type=mime_type,
            encoding=encoding,
        )
        # After the adapter, which refuses what is no RETRIEVE SelectorSpec first
        # (test_the_spec_is_checked_for_a_resource_before_its_encoding).
        _check_kernel_resource_encoding(name, binding.spec, encoding)
        template_variables: tuple[str, ...] = _template_variables(uri_template)
        # A URI-template variable is a caller-controlled name laid into the
        # read's arguments, so one the server's own seeds or the read's
        # ``request`` would occupy, or one declared twice, is refused here
        # (``test_a_template_variable_named_after_a_pool_seed_is_refused``,
        # ``test_a_uri_template_variable_named_after_a_seed_is_refused``,
        # ``test_a_duplicated_template_variable_is_refused``). A tool's
        # pagination names are deliberately not reserved: a resource has no
        # page, so ``docs://{page}`` is a legitimate locator.
        _refuse_reserved_template_variables(
            name, template_variables, reserved=self._pool_seeds.names | RESOURCE_EDGE_SEEDS
        )
        check_completions_declared(f"Resource {name!r}", binding.completions, template_variables)
        # Two ways a variable can never carry the URI's value into a read, each
        # refused here rather than at every read. A variable no read declares
        # reaches ``dispatch`` as an argument the spec does not take
        # (``test_a_template_variable_no_read_declares_is_refused_at_registration``;
        # ``parameters()`` is the reads then the shapes, so a shape is declared:
        # ``test_a_template_variable_naming_a_shape_registers_and_is_read``). A
        # variable the server fills is declared and still never the URI's: a
        # reflected ``request`` or ``view`` read is refused as an unknown argument
        # (``test_a_variable_a_reflected_read_the_server_fills_is_refused``) and a
        # key a provider always fills is replaced
        # (``test_a_variable_a_provider_always_fills_is_refused``). A name a pool
        # seed fills was refused above. ``server_owned_keys`` is the kernel's list of
        # the server's names, the one ``admitted_arguments`` subtracts for a tool.
        owned = server_owned_keys(spec, pool_seeds=self._pool_seeds)
        undeclared = sorted(set(template_variables) - spec.parameters().names())
        filled = sorted(set(template_variables) & owned)
        refusals: list[str] = []
        if undeclared:
            refusals.append(
                f"variables {undeclared} are not among the spec's reads, so every read "
                "would be refused as an argument the spec does not take; declare each "
                "as a read of the spec"
            )
        if filled:
            refusals.append(
                f"variables {filled} are names the server fills for the call (a reflected "
                "read, or a key a typed provider fills), so a value in the URI would be "
                "refused or replaced; name them differently"
            )
        if refusals:
            raise ImproperlyConfigured(
                f"Resource {name!r}: uri_template " + "; and uri_template ".join(refusals) + "."
            )
        # After the template's own checks, so a variable is refused in their
        # words: a selector no read can run as written is refused as
        # ``register_tool`` refuses one, by the kernel's reader with this
        # server's seeds
        # (``test_a_selector_no_read_can_run_is_refused_at_registration``).
        # The matcher fills every template variable on every read, so a read
        # the template names is checked as demanded, as a bound server argument
        # is (``test_an_optional_read_the_template_names_registers_and_is_read``,
        # ``test_an_optional_read_the_template_does_not_name_is_still_refused``).
        try:
            check_seeds(
                _demanding(spec, frozenset(template_variables)), pool_seeds=self._pool_seeds
            )
        except ImproperlyConfigured as exc:
            raise ImproperlyConfigured(f"Resource {name!r}: {exc}") from exc
        # The spec's own permissions are the guard, as for ``register_tool``.
        if check_kernel_spec_guarded(
            binding.name, spec, require=self._config.require_tool_permissions, kind="resource"
        ):
            object.__setattr__(binding, "unguarded", True)
        self._resources.register(binding)
        return binding

    def register_ui_resource(
        self,
        *,
        name: str,
        uri: str,
        template_name: str | None = None,
        body_template_name: str | None = None,
        diagnostics: bool | None = None,
        html: str | None = None,
        selector: Callable[[], str] | None = None,
        description: str | None = None,
        title: str | None = None,
        icons: tuple[Icon, ...] = (),
        ui: UIResourceMeta | None = None,
        mime_type: str = UI_RESOURCE_MIME_TYPE,
        permissions: Sequence[PermissionCheck] | None = None,
        scopes: str | Sequence[str] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        always_listed: bool = False,
        cache_ttl_ms: int | UnsetType = UNSET,
    ) -> ResourceBinding:
        """Register an interactive HTML view (an MCP App) as a resource.

        A tool links to the view and a **host** renders it inline in the chat,
        inside a sandboxed iframe it constructs itself. The iframe and the CSP
        *enforcement* are the host's and are deliberately not implemented here.

        The ``ui/*`` postMessage bridge is **not** in that list, and reading it
        as though it were is what this parameter set exists to prevent. The
        bridge has two ends: the host runs its end, and the *document* runs the
        other. That second end is a view's mandatory startup handshake, every
        one of its failure modes is silent, and one of them leaves the frame
        hidden with the view unable to say why.

        So give exactly one content source, and prefer the first:

        - ``body_template_name`` — a Django template holding the view's markup
          only. The package wraps it in a document whose bridge is already
          written and already correct. ``diagnostics=`` decides whether that
          bridge writes a protocol failure into the document as well as logging
          it; ``None`` follows ``settings.DEBUG``, because the text is written
          for whoever wrote the view and a rendered view's audience is whoever
          is using the product.
        - ``template_name`` — a Django template holding a whole document, whose
          bridge is then yours to write.
        - ``html`` — a literal document, same.
        - ``selector`` — a callable returning one, which **must take no
          arguments**: a selector that declares a parameter is refused at
          registration, because the read path would fill it from a pool carrying
          ``request`` and ``user``.

        **Keep tenant data out of the view.** Hosts may prefetch and cache a
        view before any tool call, so it is a shell that hydrates itself at
        runtime from tool results — which is also why the template renders with
        no context.

        ``ui=`` is the typed
        [`UIResourceMeta`][django_spec_mcp.registry.types.ui_resource_meta.UIResourceMeta]
        — CSP origins, browser permissions, publisher ``domain``, border preference —
        which serialises into ``_meta`` under the extension's key. ``meta=`` remains
        available for *other* extensions; passing both ``ui=`` and that same key inside
        ``meta=`` raises, rather than letting one silently win.

        The result is an ordinary
        [`ResourceBinding`][django_spec_mcp.registry.types.resource_binding.ResourceBinding],
        so it shares one URI namespace with data resources (a collision raises as
        always), appears in ``resources/list``, and honours ``permissions`` /
        ``scopes`` / ``always_listed``. Views default to **unguarded** — the MCP
        session is already authenticated and a view is a static asset, not tenant
        data.

        ``permissions`` are kernel ``PermissionCheck``s, the instances a spec
        carries, each asked ``has_permission(principal, binding)`` with the view's
        ``ResourceBinding`` as the spec; ``scopes`` are the OAuth scopes the
        caller's token must carry, every one, asked first. Both gate the listing,
        the read and a subscription alike."""
        binding = ui_view_to_resource(
            name=name,
            uri=uri,
            template_name=template_name,
            body_template_name=body_template_name,
            diagnostics=diagnostics,
            html=html,
            selector=selector,
            description=description,
            title=title,
            icons=icons,
            ui=ui,
            mime_type=mime_type,
            permissions=binding_checks(f"Resource {name!r}", permissions or ()),
            scopes=scopes,
            rate_limits=tuple(rate_limits or ()),
            annotations=annotations,
            meta=meta,
            always_listed=always_listed,
            cache_ttl_ms=cache_ttl_ms,
        )
        # No ``check_tool_permissions_declared`` here, unlike every other
        # registration on this server. A view's content sources are a template
        # rendered with no context (whole document or body fragment — the shell
        # around a fragment is the package's own and reads nothing), a literal
        # document, or a zero-argument callable — none of which can read the
        # caller's data, so an unguarded view exposes nothing an authenticated
        # session may not already see.
        # Requiring permissions on it would be noise on every MCP Apps install.
        #
        # That third source is only caller-blind because ``ui_view_to_resource``
        # refuses a selector that declares any fillable parameter. It did not
        # always: ``handle_resources_read`` resolves every binding's selector
        # against a pool carrying ``request`` and ``user``, so
        # ``selector=lambda user: ...`` was handed the caller while this comment
        # said it could not be — and skipped the declaration check on the
        # strength of the claim. The exemption is sound *because* of that
        # refusal, not on its own, and the two have to move together.
        self._resources.register(binding)
        return binding

    def register_prompt(
        self,
        *,
        name: str,
        render: Callable[..., Any],
        description: str | None = None,
        title: str | None = None,
        icons: tuple[Icon, ...] = (),
        arguments: list[PromptArgument] | None = None,
        completions: dict[str, Callable[..., Any]] | None = None,
        permissions: Sequence[PermissionCheck] | None = None,
        scopes: str | Sequence[str] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        always_listed: bool = False,
    ) -> PromptBinding:
        """Register a render callable as an MCP prompt.

        ``render`` receives the prompt arguments as kwargs (plus ``request`` and
        ``user`` if it declares them) and returns either a string, a list of strings, a
        list of
        [`PromptMessage`][django_spec_mcp.protocol.types.prompt_message.PromptMessage],
        or a coroutine yielding any of those — the dispatch layer normalises the result.

        ``request`` and ``user`` are seeded **over** the client's arguments at
        ``prompts/get``, so an argument named after one of them never reaches
        ``render`` in the seed's place.

        ``permissions`` are kernel ``PermissionCheck``s, the instances a spec
        carries: a prompt is not a spec, so each is asked
        ``has_permission(principal, binding)`` with the prompt's
        ``PromptBinding`` as the spec, and a check on the principal alone answers
        a prompt as it answers a spec. ``scopes`` are the OAuth scopes the
        caller's token must carry, every one, asked first; a bare string is one
        scope. Both gate ``prompts/list``, ``prompts/get`` and a completion alike.

        A prompt with neither is refused like a tool — see
        ``REQUIRE_TOOL_PERMISSIONS``; ``permissions=[Unrestricted()]`` is the
        way to say it is open. A ``render`` callable reads whatever its
        author gave it access to, so nothing about a prompt makes it safe by
        construction.

        ``meta`` is the generic ``_meta`` bundle for this prompt's
        ``prompts/list`` entry — see ``register_tool``.
        """
        binding = PromptBinding(
            name=name,
            description=description,
            title=title,
            icons=icons,
            render=render,
            arguments=tuple(arguments or ()),
            permissions=binding_checks(f"Prompt {name!r}", permissions or ()),
            scopes=scope_names(f"Prompt {name!r}", scopes),
            rate_limits=tuple(rate_limits or ()),
            annotations=annotations or {},
            meta=merge_meta(meta),
            always_listed=always_listed,
            completions=dict(completions or {}),
        )
        check_completions_declared(
            f"Prompt {name!r}", binding.completions, (arg.name for arg in binding.arguments)
        )
        check_tool_permissions_declared(
            binding.name,
            guarded=bool(binding.permissions or binding.scopes),
            require=self._config.require_tool_permissions,
            kind="prompt",
        )
        self._prompts.register(binding)
        return binding

    # ----- declarative (decorator) registration -----

    def prompt(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        title: str | None = None,
        icons: tuple[Icon, ...] = (),
        arguments: list[PromptArgument] | None = None,
        completions: dict[str, Callable[..., Any]] | None = None,
        permissions: Sequence[PermissionCheck] | None = None,
        scopes: str | Sequence[str] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        always_listed: bool = False,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form: register the wrapped callable as a prompt."""

        def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.register_prompt(
                name=name or getattr(fn, "__name__", "prompt"),
                render=fn,
                description=description or fn.__doc__,
                title=title,
                icons=icons,
                arguments=arguments,
                completions=completions,
                permissions=permissions,
                scopes=scopes,
                rate_limits=rate_limits,
                annotations=annotations,
                meta=meta,
                always_listed=always_listed,
            )
            return fn

        return wrap

    # ----- accessors -----

    @property
    def tools(self) -> ToolRegistry:
        return self._tools

    @property
    def resources(self) -> ResourceRegistry:
        return self._resources

    @property
    def prompts(self) -> PromptRegistry:
        return self._prompts

    @property
    def config(self) -> MCPConfig:
        """This server's resolved scalars — a frozen snapshot taken at construction."""
        return self._config

    @property
    def pool_seeds(self) -> PoolSeeds:
        """The ``pool_seeds=`` every dispatch and condition on this server receives."""
        return self._pool_seeds

    @property
    def conventions(self) -> AgentConventions:
        """The ``conventions=`` every listing and refusal on this server is worded with."""
        return self._conventions

    @property
    def auth_backend(self) -> MCPAuthBackend:
        return self._auth_backend

    @property
    def session_store(self) -> SessionStore:
        return self._session_store

    @property
    def sse_broker(self) -> SSEBroker:
        return self._sse_broker

    @property
    def sse_replay_buffer(self) -> SSEReplayBuffer | None:
        return self._sse_replay_buffer

    # ----- server-initiated push -----

    async def notify(self, session_id: str, payload: Any) -> bool:
        """Push a JSON-RPC payload to a session's open SSE stream.

        Returns ``True`` if a subscriber was present, ``False`` if no client is
        connected — a missed push is not generally an error, since clients pull
        state via ``tools/call`` round-trips. The broker enforces one
        subscriber per session: re-subscribing replaces the old queue silently.

        With a
        [`SSEReplayBuffer`][django_spec_mcp.transport.types.sse_replay_buffer.SSEReplayBuffer]
        configured the payload is recorded *before* publishing, so the frame carries an
        event ID the SSE generator emits on the wire (``id: <id>\\ndata:
        <payload>\\n\\n``) and a later reconnect with ``Last-Event-ID`` drains what it
        missed before resuming live mode. Without a buffer there are no ``id:`` lines
        and resume is disabled.

        Multi-process deployments need an out-of-process broker to fan out
        across workers; the in-process broker only sees its own.
        """
        if self._sse_replay_buffer is None:
            return await self._sse_broker.publish(session_id, payload)
        event_id: str = await self._sse_replay_buffer.record(session_id, payload)
        # Wrapped so the generator can emit ``id:`` alongside ``data:``;
        # unwrapped in ``stream_events``. Brokers never see the wrapper shape.
        return await self._sse_broker.publish(
            session_id,
            {"_mcp_event_id": event_id, "_mcp_payload": payload},
        )

    # ----- URLs -----

    @property
    def urls(self) -> tuple[list[URLPattern], str, str]:
        """Sync URL patterns. Suitable for any deployment (WSGI or ASGI).

        Returns the namespaced ``(patterns, app_name, namespace)`` triple
        ``path()`` mounts directly — ``path("mcp/", server.urls)``, the
        ``admin.site.urls`` idiom — so the endpoints reverse within the
        namespace (``reverse("mcp:endpoint")``). Use ``async_urls`` instead
        when running under ASGI to get non-blocking dispatch for the I/O-bound
        handlers.
        """
        view = StreamableHttpViewSet.as_view(
            STREAMABLE_HTTP_ACTION_MAP,
            tools=self._tools,
            resources=self._resources,
            prompts=self._prompts,
            auth_backend=self._auth_backend,
            session_store=self._session_store,
            task_store=self._task_store,
            task_executor=self._task_executor,
            # Without this a tool's ``invalidates=`` announcements are silently
            # dropped on WSGI: the viewset has nowhere to publish them, and a
            # dropped announcement looks exactly like a tool that changed nothing.
            # ``async_urls`` has always passed it.
            subscription_broker=self._subscription_broker,
            pool_seeds=self._pool_seeds,
            conventions=self._conventions,
            server_info=self._server_info,
            instructions=self.description,
            config=self._config,
        )
        return self._urls_with_view(view)

    @property
    def async_urls(self) -> tuple[list[URLPattern], str, str]:
        """Async URL patterns for ASGI deployments.

        The namespaced triple (like ``urls``), but ``tools/call``,
        ``resources/read`` and ``prompts/get`` dispatch through async-native
        runners. Sync collaborators (auth backend, session store, custom
        permissions) are bridged via ``asgiref.sync.sync_to_async``, so a
        fully sync stack still works; async-native ones are detected by
        signature and called directly.
        """
        view = AsyncStreamableHttpViewSet.as_view(
            ASYNC_STREAMABLE_HTTP_ACTION_MAP,
            tools=self._tools,
            resources=self._resources,
            prompts=self._prompts,
            auth_backend=self._auth_backend,
            session_store=self._session_store,
            task_store=self._task_store,
            task_executor=self._task_executor,
            subscription_broker=self._subscription_broker,
            pool_seeds=self._pool_seeds,
            conventions=self._conventions,
            sse_broker=self._sse_broker,
            sse_replay_buffer=self._sse_replay_buffer,
            server_info=self._server_info,
            instructions=self.description,
            config=self._config,
        )
        return self._urls_with_view(view)

    def _urls_with_view(self, view: Any) -> tuple[list[URLPattern], str, str]:
        # Mounting is where the auth backend stops being optional: from here on
        # every request reaches ``authenticate``. Both ``urls`` and
        # ``async_urls`` land here, so one call covers them. A backend that does
        # not implement the opt-in protocol needs no setup and is left alone.
        if isinstance(self._auth_backend, SelfChecking):
            self._auth_backend.check_configuration()
        patterns = [
            path("", view, name="endpoint"),
            path(
                ".well-known/oauth-protected-resource",
                ProtectedResourceMetadataViewSet.as_view(
                    {"get": "list"}, auth_backend=self._auth_backend
                ),
                name="protected-resource-metadata",
            ),
        ]
        return patterns, self._url_namespace, self._url_namespace


def _caller_stacklevel() -> int:
    """The ``stacklevel`` that points a warning at the first frame outside this package.

    A registration reaches its warning through ``register_tool`` or
    ``register_tools``, each a different depth, so a fixed level would blame
    this package for one of them. Counted from the frame that calls
    ``warnings.warn``, which is level 1, and the description and pagination
    checks add one for their own frame
    (``test_the_warning_points_at_the_registering_line_not_this_package`` in the
    undescribed and unbounded-list suites).
    """
    package = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + os.sep
    frame = sys._getframe(1)
    level = 1
    while frame.f_back is not None and frame.f_code.co_filename.startswith(package):
        frame = frame.f_back
        level += 1
    return level


def _refuse_pagination_named_reads(binding: KernelToolBinding) -> None:
    """Refuse a paginated kernel LIST tool whose spec declares ``page`` or ``limit``.

    The read pipeline owns both names on a tool that paginates: the listing
    advertises them as the page and its size, and the call takes them from the
    arguments before the selector runs, so a read of either name registered,
    was advertised as the pipeline's integer and never received the caller's
    value. Only under ``paginate=True``: an unpaginated LIST tool hands either
    name to its selector as any read
    (``test_an_unpaginated_list_tools_pagination_named_read_reaches_the_selector``).
    """
    if not binding.paginate:
        return
    taken = sorted(binding.spec.parameters().names() & RESERVED_POST_FETCH_KEYS)
    if taken:
        raise ImproperlyConfigured(
            f"MCP tool {binding.name!r}: the spec declares the read(s) {taken!r}, but `page` "
            "and `limit` belong to the pagination of a tool registered with paginate=True, "
            "which takes them from the arguments before the selector is called, so the "
            "read would never receive the caller's value. Rename the read."
        )


def _as_called(
    spec: KernelServiceSpec | KernelSelectorSpec, bound: frozenset[str]
) -> KernelServiceSpec | KernelSelectorSpec:
    """``spec`` as a call on this server meets it: every read in ``bound`` demanded.

    ``bound`` is a binding's ``server_arguments.values``, which replace the
    caller's arguments of those names on every call, so such a read is never
    left out whatever it declares. ``check_seeds`` reads a read declared
    optional over a keyword with no default as one a call may leave out, and
    so refuses it; asked of this copy instead, it refuses only the reads the
    server does not fill
    (``test_an_optional_read_the_server_binds_registers_and_is_filled``,
    ``test_an_optional_read_the_server_does_not_bind_is_still_refused``). A
    target selector's reads are the call's arguments too, so its seat is read
    the same way (``test_an_optional_lookup_read_the_server_binds_registers_and_is_filled``,
    one case per seat). Only for the check: the binding keeps the declared spec.
    """
    if isinstance(spec, KernelServiceSpec):
        if spec.instance_selector_spec is not None:
            spec = spec.derive(
                instance_selector_spec=cast(
                    KernelSelectorSpec, _demanding(spec.instance_selector_spec, bound)
                )
            )
        if spec.collection_selector_spec is not None:
            spec = spec.derive(
                collection_selector_spec=cast(
                    KernelSelectorSpec, _demanding(spec.collection_selector_spec, bound)
                )
            )
    return _demanding(spec, bound)


def _demanding(
    spec: KernelServiceSpec | KernelSelectorSpec, bound: frozenset[str]
) -> KernelServiceSpec | KernelSelectorSpec:
    """``spec`` with each of its own reads named in ``bound`` required, and declaring no default.

    ``bound`` is what is sent on every call: a binding's ``server_arguments``
    values, or a resource's URI-template variables. The default is dropped as
    well, because ``check_seeds`` demands a read only when it is required and
    declares none
    (``test_a_bound_read_declaring_a_default_registers_and_takes_the_bound_value``).
    """
    if not bound & spec.reads.names():
        return spec
    return spec.derive(
        reads=Parameters.of(
            *(
                replace(read, required=True, default=UNSET) if read.name in bound else read
                for read in spec.reads
            )
        )
    )


def _check_kernel_resource_encoding(
    name: str, spec: KernelSelectorSpec, encoding: ResourceEncoding
) -> None:
    """Refuse a non-JSON ``encoding`` beside what only a JSON body can carry.

    ``TEXT`` and ``BLOB`` serve what the presenter returns as a document or as
    bytes, and two declarations make that value something else on some read:

    - ``affordances``, which the kernel's ``present`` adds to each presented
      object, refusing a ``str`` or bytes on every read
      (``test_a_non_json_encoding_beside_affordances_is_refused``);
    - ``allow_none``, under which a missing row presents as ``None`` without
      the presenter running, so the read would blame a presenter that never
      ran (``test_a_non_json_encoding_beside_allow_none_is_refused``).

    Under ``JSON`` both are bodies, an object carrying its affordances and a
    ``null``, so both register (``test_a_json_resource_keeps_affordances_and_allow_none``).
    """
    if encoding is ResourceEncoding.JSON:
        return
    if spec.affordances is not None:
        raise ImproperlyConfigured(
            f"Resource {name!r}: encoding={encoding.name} cannot sit beside affordances. "
            "The kernel adds each affordance to the presented object, and a "
            f"{encoding.name} body is not an object; serve it as JSON, or drop the "
            "affordances."
        )
    if spec.allow_none:
        raise ImproperlyConfigured(
            f"Resource {name!r}: encoding={encoding.name} cannot sit beside allow_none. "
            "A missing row presents as None without the presenter running, which a "
            f"{encoding.name} body cannot be; drop allow_none so a missing row is "
            "resource-not-found, or serve it as JSON."
        )


def _refuse_reserved_template_variables(
    name: str, variables: tuple[str, ...], *, reserved: frozenset[str]
) -> None:
    """Refuse a URI-template variable a seed occupies, or one declared twice.

    ``reserved`` is the server's own seeds and the read's edge seeds; the
    kernel's ``RESERVED_POOL_SEEDS`` are always added, since they are the
    dispatcher's authoritative names, and a URI segment routed onto one would
    stand in for the caller's identity. A name declared twice would have the
    later segment silently replace the earlier.
    """
    taken = RESERVED_POOL_SEEDS | reserved
    collisions = sorted(set(variables) & taken)
    if collisions:
        raise ImproperlyConfigured(
            f"Resource {name!r}: uri_template variable name(s) {collisions} collide with "
            f"reserved transport keys {sorted(taken)}. Rename them."
        )
    duplicates = sorted({variable for variable in variables if variables.count(variable) > 1})
    if duplicates:
        raise ImproperlyConfigured(
            f"Resource {name!r}: duplicate uri_template variable name(s) {duplicates}."
        )


def _template_variables(uri_template: str) -> tuple[str, ...]:
    """The ``{var}`` names in a URI template — a resource's completable arguments.

    Not shared with ``ResourceRegistry``: that one compiles templates to
    matching regexes, and this only needs the names.
    """
    return tuple(re.findall(r"\{([^}]+)\}", uri_template))


__all__ = ["MCPServer"]
