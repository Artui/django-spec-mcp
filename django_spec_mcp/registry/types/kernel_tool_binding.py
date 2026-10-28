from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    UNSET,
    AudienceProjection,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceSpec,
    UnknownArguments,
    UnsetType,
    audience_projection_for_spec,
)

from django_spec_mcp.protocol.types.icon import Icon
from django_spec_mcp.registry.types.utils import kernel_scope_gate, validate_content_kind
from django_spec_mcp.types.output_format import OutputFormat
from django_spec_mcp.types.task_policy import TaskPolicy
from django_spec_mcp.types.tool_content_kind import ToolContentKind


@dataclass(frozen=True)
class KernelToolBinding:
    """All wiring for one MCP tool whose operation is a kernel spec.

    ``spec`` is a ``django_service_specs`` ``ServiceSpec`` or ``SelectorSpec``, and
    its class says which kind of tool this is: a service tool runs a mutation, a
    selector tool reads. The call goes through the kernel's ``dispatch`` /
    ``adispatch``, and the advertised schemas are the kernel's own
    ``spec_input_schema`` / ``spec_output_schema``, so the arguments a service
    tool takes are the spec's parameters, spread at the top level of
    ``arguments`` rather than nested under a ``data`` key.

    Every other field is a fact of the MCP binding rather than of the operation,
    and carries the meaning the same-named field has on ``ToolBinding`` /
    ``SelectorToolBinding``. The names the shared ``tools/call`` shell reads off
    any binding (``permissions``, ``rate_limits``, ``server_arguments``,
    ``task_policy``, ``invalidates``, ``dispatch_timeout``, ``max_result_bytes``,
    ``url_kwargs`` and ``query_params``) are kept, so the permission check, the
    rate limits, the deadline, the result ceiling and the invalidation run for a
    kernel binding exactly as for the others, and only the dispatch differs.
    """

    name: str
    description: str | None
    spec: ServiceSpec | SelectorSpec
    title: str | None = None
    display_name: str | None = None
    """Consumer-only label, **never emitted on the MCP wire**, so a downstream
    library can render a richer label than the protocol ``title``."""

    display_description: str | None = None
    """Consumer-only blurb, the sibling of ``display_name`` and likewise
    never emitted on the MCP wire."""

    icons: tuple[Icon, ...] = ()
    annotations: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    output_format: OutputFormat = OutputFormat.JSON
    content_kind: ToolContentKind = ToolContentKind.TEXT
    content_mime_type: str | None = None
    include_structured_content: bool | None = None
    include_output_schema: bool | None = None
    invalidates: tuple[str, ...] = ()
    """Service tools only; see ``ToolBinding.invalidates``."""
    paginate: bool = False
    """``LIST`` selector tools only; see ``SelectorToolBinding.paginate``."""
    max_page_size: int | None | UnsetType = UNSET
    """``LIST`` selector tools only; see ``SelectorToolBinding.max_page_size``."""
    max_result_bytes: int | None | UnsetType = UNSET
    dispatch_timeout: float | None | UnsetType = UNSET
    always_listed: bool = False
    rate_limits: tuple[Any, ...] = ()
    scopes: tuple[str, ...] | None = None
    """The OAuth scopes a caller's token must carry, every one of them.

    ``None`` is no scope gate. Enforced as the ``ScopeRequired`` check on
    ``permissions``, so a token missing one is answered ``-32006`` with every
    declared scope in ``requiredScopes``, not only the missing ones, as a
    ``ScopeRequired`` permission is answered on every other binding. An empty
    tuple is refused when the binding is built, by hand or by the registrar: it
    would read as a gate and admit every caller."""
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT
    """Handed to the kernel's ``dispatch`` as ``unknown_arguments=``, and read by
    ``spec_input_schema`` so the advertised schema is closed exactly when the
    call refuses an undeclared key."""
    server_arguments: ServerArguments = field(default_factory=ServerArguments)
    """See ``ToolBinding.server_arguments``. Applied by the same shell step
    (``handlers.utils.apply_tool_server_arguments``) and left out of the
    advertised ``inputSchema``."""
    task_policy: TaskPolicy = TaskPolicy.FORBIDDEN
    """See ``ToolBinding.task_policy``. Read by ``maybe_create_task``, which asks
    a kernel spec's own checks before it creates the task; the worker runs the
    call, under either policy, through the same ``dispatch`` an inline call takes."""
    permissions: tuple[Any, ...] = field(init=False)
    """The transport checks the shared shell judges, derived from ``scopes``.

    Never the spec's own ``permissions``: those are the kernel's, and
    ``dispatch`` asks them itself."""
    unguarded: bool = field(default=False, init=False)
    """Whether a call runs the spec without a permission check.

    Set only by the registrar, when it registers a spec declaring
    ``permissions=None`` with ``REQUIRE_TOOL_PERMISSIONS`` off, so nothing can set
    it at construction: a binding built by hand stays unmarked, and the kernel's
    dispatch refuses its unguarded spec
    (``test_a_handbuilt_unguarded_kernel_tool_is_never_called``)."""

    def __post_init__(self) -> None:
        # Each conjunct is held by a case of
        # test_the_binding_takes_either_half_of_the_refused_pair_alone.
        if self.include_output_schema is True and self.include_structured_content is False:
            raise ImproperlyConfigured(
                f"Tool {self.name!r}: include_output_schema=True is incompatible "
                "with include_structured_content=False. The MCP spec requires that "
                "any tool advertising outputSchema also return conforming "
                "structuredContent. Set one of them differently."
            )
        validate_content_kind(
            name=self.name,
            content_kind=self.content_kind,
            content_mime_type=self.content_mime_type,
            include_structured_content=self.include_structured_content,
            include_output_schema=self.include_output_schema,
        )
        # Here rather than in the registrar, so a binding built by hand reads a
        # bare string as one scope and refuses ``scopes=()`` as well
        # (``test_a_binding_built_by_hand_reads_a_bare_string_scope_as_one``).
        scopes, checks = kernel_scope_gate(f"Tool {self.name!r}", self.scopes)
        object.__setattr__(self, "scopes", scopes)
        object.__setattr__(self, "permissions", checks)

    @property
    def is_selector(self) -> bool:
        """Whether this is a selector tool, a read, rather than a service tool."""
        return isinstance(self.spec, SelectorSpec)

    @property
    def kind(self) -> SelectorKind | None:
        """A selector tool's ``RETRIEVE`` / ``LIST``; ``None`` for a service tool."""
        return self.spec.kind if isinstance(self.spec, SelectorSpec) else None

    @property
    def url_kwargs(self) -> tuple[()]:
        """Always empty: a kernel spec declares its own reads, so no argument is
        routed to a view's ``kwargs``. Read by the shared shell's
        ``dispatch_shape``."""
        return ()

    @property
    def query_params(self) -> tuple[()]:
        """Always empty, for the reason ``url_kwargs`` is."""
        return ()

    @cached_property
    def audience_projection(self) -> AudienceProjection:
        """The spec's resolved audience markings, derived once per binding.

        The kernel's own ``audience_projection_for_spec``, so the projected
        payload and the advertised ``outputSchema`` read the same markings."""
        return audience_projection_for_spec(self.spec, name=f"Tool {self.name!r}")


__all__ = ["KernelToolBinding"]
