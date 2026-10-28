"""Adapter-layer helpers shared by the kernel adapters.

The ``merge_*`` functions fold the several contributions a binding assembles
(tool annotations, ``_meta`` bundles) into the single dict the wire types emit.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def merge_tool_annotations(
    explicit: dict[str, Any] | None, *, read_only: bool, idempotent: bool | None = None
) -> dict[str, Any]:
    """Auto-derive a tool's MCP ``ToolAnnotations``, explicit hints winning.

    A tool's mutation profile is known from its kind, so the standard MCP hints
    are stamped here rather than hand-set downstream:

    - ``read_only=True`` (selector tools) → ``{"readOnlyHint": True}``. ``destructiveHint`` /
      ``idempotentHint`` are deliberately *not* emitted — the MCP spec defines
      them as meaningful only when ``readOnlyHint`` is false — so ``idempotent``
      is ignored here.
    - ``read_only=False`` (service tools) →
      ``{"readOnlyHint": False, "destructiveHint": True}``. A mutation is
      destructive by default.

    ``idempotent`` is a service spec's declared ``ServiceSpec.idempotent``,
    which a service tool passes through. A declared ``True`` or ``False``
    becomes ``idempotentHint`` on a mutation; ``None`` (undeclared, the
    default) leaves the hint absent, and a client reads its absence as
    ``false``, the MCP default. The kernel keeps ``None`` apart from
    ``False`` so that a transport publishing the fact never turns silence into
    a claim, which is why a declared ``False`` is published rather than
    dropped.

    Both conjuncts of the derivation are held by a test, because a deleted
    one leaves branch coverage at 100%:
    ``test_a_read_only_tool_never_derives_the_idempotent_hint`` holds
    ``not read_only``, and ``test_an_undeclared_service_spec_leaves_the_hint_absent``
    holds ``idempotent is not None``.

    Any hint supplied at registration via ``annotations=`` overrides the derived
    default: a non-destructive mutation passes
    ``annotations={"destructiveHint": False}``, an undeclared spec can still
    add ``{"idempotentHint": True}``, and either kind can set ``title`` /
    ``openWorldHint``. The result is stored on the binding, so it is the single
    source of truth for ``tools/list`` and for anything reading
    ``binding.annotations``.
    """
    derived: dict[str, Any] = (
        {"readOnlyHint": True} if read_only else {"readOnlyHint": False, "destructiveHint": True}
    )
    if not read_only and idempotent is not None:
        derived["idempotentHint"] = idempotent
    return {**derived, **(explicit or {})}


def merge_meta(*pieces: Mapping[str, Any] | None) -> dict[str, Any]:
    """Shallow-merge ``_meta`` contributions into one bundle, later wins.

    ``_meta`` is the base protocol's open extension namespace, where each
    extension owns a top-level key and several sources may contribute at once:
    the ``meta=`` a consumer passes at registration plus whatever a framework
    feature derives. Combining them here means a later feature injects its key
    by adding one argument at the adapter call site.

    Semantics, deliberately narrow:

    - **Shallow**, one level deep. A later piece replaces an earlier one's value
      for the same top-level key outright rather than deep-merging into it —
      extension keys are opaque bundles owned by one extension, and splicing two
      together produces a shape neither owner declared.
    - **Later wins**, so call sites read as precedence order: framework piece
      first and the consumer's ``meta=`` last for "consumer overrides", the
      reverse when the framework must win.
    - ``None`` and empty pieces are skipped, and the result is always a new dict,
      so no piece is mutated and no caller shares a mutable default.
    """
    merged: dict[str, Any] = {}
    for piece in pieces:
        if piece:
            merged.update(piece)
    return merged


__all__ = [
    "merge_meta",
    "merge_tool_annotations",
]
