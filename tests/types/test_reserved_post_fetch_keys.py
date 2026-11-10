from __future__ import annotations

from django_service_specs import RESERVED_POOL_SEEDS

from django_spec_mcp.types.reserved_post_fetch_keys import RESERVED_POST_FETCH_KEYS


def test_reserved_post_fetch_keys_shape() -> None:
    # Every name in here is a name a selector cannot use for a parameter of its
    assert frozenset({"page", "limit"}) == RESERVED_POST_FETCH_KEYS


def test_reserved_sets_are_disjoint() -> None:
    """Post-fetch keys and pool seeds describe distinct concerns; no overlap."""
    assert not (RESERVED_POST_FETCH_KEYS & RESERVED_POOL_SEEDS)
