"""``COMPLETION_EDGE_SEEDS``: the sibling names a completion drops beyond the kernel's set."""

from __future__ import annotations

from django_service_specs import RESERVED_POOL_SEEDS
from rest_framework_services.dispatch.utils import DROPPED_EDGE_SEEDS

from django_spec_mcp.types.completion_edge_seeds import COMPLETION_EDGE_SEEDS


def test_the_completion_edge_seeds_are_what_drf_services_drops() -> None:
    # Written out in ``types/completion_edge_seeds.py``, where the completion read
    # drf-services' internal set before; pinned to it so neither moves alone.
    assert DROPPED_EDGE_SEEDS == COMPLETION_EDGE_SEEDS
    assert not (COMPLETION_EDGE_SEEDS & RESERVED_POOL_SEEDS)
