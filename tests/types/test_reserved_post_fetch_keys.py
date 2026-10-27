"""``RESERVED_POST_FETCH_KEYS``: what a list tool's post-fetch pipeline consumes."""

from __future__ import annotations

from django_service_specs import RESERVED_POOL_SEEDS

from django_spec_mcp.types.reserved_post_fetch_keys import RESERVED_POST_FETCH_KEYS


def test_reserved_post_fetch_keys_shape() -> None:
    # ``ordering`` left when ``ordering_fields`` did: the pipeline stopped
    # consuming it, and every name in here is a name a selector cannot use for a
    # parameter of its own -- so an entry the pipeline no longer reads is pure
    # cost, and was silently dropping a value reflection had advertised.
    assert frozenset({"page", "limit"}) == RESERVED_POST_FETCH_KEYS


def test_reserved_sets_are_disjoint() -> None:
    """Post-fetch keys and pool seeds describe distinct concerns; no overlap."""
    assert not (RESERVED_POST_FETCH_KEYS & RESERVED_POOL_SEEDS)
