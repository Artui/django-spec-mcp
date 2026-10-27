"""Names this package reads from the packages that own them, pinned to their shape.

The root re-exports ``ArgumentBinding``, drf-services', and ``UnknownArguments``,
the kernel's. ``RESERVED_POOL_SEEDS`` is the kernel's too and is read from it
directly. Each is pinned to its full shape here, so an upstream change to one
fails a test in this package rather than moving a name it uses without a word.
"""

from __future__ import annotations

from django_service_specs import RESERVED_POOL_SEEDS

from django_spec_mcp import ArgumentBinding, UnknownArguments


def test_argument_binding_re_exports_drf_services_members() -> None:
    """``ArgumentBinding`` is re-exported from drf-services (the dispatch core)."""
    members = {m.name for m in ArgumentBinding}
    assert members == {"AUTO", "BUNDLE", "SPREAD_AUTHOR_WINS", "SPREAD_CALLER_WINS"}


def test_argument_binding_members_identity() -> None:
    assert ArgumentBinding.BUNDLE is ArgumentBinding.BUNDLE
    assert ArgumentBinding.SPREAD_AUTHOR_WINS is not ArgumentBinding.BUNDLE
    assert ArgumentBinding.SPREAD_CALLER_WINS is not ArgumentBinding.SPREAD_AUTHOR_WINS


def test_argument_binding_str_value_is_internal_only() -> None:
    """The string-shaped value is internal — never expected at call sites.

    ``ArgumentBinding`` is a plain ``Enum`` (not ``str, Enum``), so members
    don't compare equal to their string values. This is intentional —
    forces API consumers to pass the enum member.
    """
    assert ArgumentBinding.SPREAD_AUTHOR_WINS != "spread_author_wins"
    assert ArgumentBinding.SPREAD_AUTHOR_WINS.value == "spread_author_wins"


def test_reserved_pool_seeds_shape() -> None:
    # Re-exported from django-service-specs, which owns the set. The local copy
    # this once replaced had fallen a key behind it (``collection``) — pinning the
    # shape here is what would have caught that, so keep asserting the full set
    # rather than deferring to the import. drf-services 0.58.0 ("Breaking: pool
    # seeds") swapped ``request`` and ``serializer`` for ``result`` and
    # ``queryset``: the DRF edge seeds ``request`` itself, which is why
    # ``RESOURCE_EDGE_SEEDS`` exists. The kernel's 0.14.0 reserves
    # ``call_context``, the one dict a call hands to every stage naming it.
    assert (
        frozenset(
            {
                "user",
                "data",
                "instance",
                "collection",
                "result",
                "queryset",
                "progress",
                "call_context",
            }
        )
        == RESERVED_POOL_SEEDS
    )


def test_unknown_arguments_has_three_members() -> None:
    assert {m.name for m in UnknownArguments} == {"REJECT", "PASSTHROUGH", "IGNORE"}


def test_unknown_arguments_is_a_str_enum() -> None:
    # The kernel's own enum, a ``str`` enum since drf-services 0.58.0 ("``UnknownArguments``
    # is a ``str`` enum ordered ``REJECT``, ``IGNORE``, ``PASSTHROUGH``"), where the
    # earlier plain ``Enum`` compared unequal to its value.
    assert isinstance(UnknownArguments.REJECT, str)
    assert UnknownArguments.REJECT == "reject"
    assert UnknownArguments.PASSTHROUGH.value == "passthrough"
    assert [m.name for m in UnknownArguments] == ["REJECT", "IGNORE", "PASSTHROUGH"]
