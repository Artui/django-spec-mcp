from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django_service_specs import PermissionCheck

from django_spec_mcp.protocol.types.icon import Icon
from django_spec_mcp.protocol.types.prompt_argument import PromptArgument
from django_spec_mcp.registry.types.utils import binding_checks, scope_names


@dataclass(frozen=True)
class PromptBinding:
    """All wiring for a single MCP prompt.

    A prompt is a server-defined message template the client invokes by name. The
    ``render`` callable receives the client-supplied arguments as kwargs and returns a
    list of
    [`PromptMessage`][django_spec_mcp.protocol.types.prompt_message.PromptMessage]
    instances, a list of strings (each becoming a user text message), a single string,
    or a coroutine yielding any of those. The handler normalises whichever shape arrives
    into the spec's ``messages`` list at dispatch time.

    ``annotations`` and ``meta`` are emitted verbatim on this prompt's
    ``prompts/list`` entry, under ``annotations`` and ``_meta`` respectively.
    """

    name: str
    description: str | None
    render: Callable[..., Any]
    arguments: tuple[PromptArgument, ...] = ()
    permissions: tuple[PermissionCheck, ...] = ()
    """The kernel ``PermissionCheck``s a caller must pass, every one of them.

    A prompt is not a spec, so nothing dispatches it: each check is asked
    ``has_permission(principal, binding)`` with this binding as the spec
    (``handlers.judge_binding``), at ``prompts/list``, ``prompts/get`` and a
    completion of one of its arguments alike. A check on the principal alone,
    the usual case, is the same instance a spec carries. Refused when built by
    the kernel's rule for a spec's (``registry.types.utils.binding_checks``)."""
    scopes: tuple[str, ...] | None = None
    """The OAuth scopes a caller's token must carry, every one of them, asked
    before ``permissions``. ``None`` is no scope gate; a bare string is one scope,
    and ``()`` is refused, as on ``KernelToolBinding.scopes``."""
    rate_limits: tuple[Any, ...] = ()
    annotations: dict[str, Any] = field(default_factory=dict)
    # Free-form for the reason given on ``KernelToolBinding.meta``.
    meta: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    completions: dict[str, Callable[..., Any]] = field(default_factory=dict)
    """Argument name to completer callable, powering ``completion/complete``.

    A completer is dispatched through ``resolve_callable_kwargs`` against a
    pool of ``value`` (the text typed so far), ``arguments`` (siblings the
    client has already resolved, also spread by name), ``request`` and
    ``user``. It returns an iterable of suggestions — a list, a generator or a
    queryset — which the handler slices to the spec's cap rather than
    draining."""

    icons: tuple[Icon, ...] = ()
    """Display icons, emitted in this prompt's listing entry. Purely
    presentational; nothing in dispatch reads them."""

    always_listed: bool = False
    """Keep this prompt in ``prompts/list`` even when ``FILTER_LISTINGS_BY_PERMISSIONS``
    would hide it — same semantics as
    [`KernelToolBinding.always_listed`][django_spec_mcp.registry.types.kernel_tool_binding.KernelToolBinding.always_listed]."""

    def __post_init__(self) -> None:
        label = f"Prompt {self.name!r}"
        object.__setattr__(self, "scopes", scope_names(label, self.scopes))
        object.__setattr__(self, "permissions", binding_checks(label, self.permissions or None))


__all__ = ["PromptBinding"]
