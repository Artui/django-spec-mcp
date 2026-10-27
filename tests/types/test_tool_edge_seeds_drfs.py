"""``TOOL_EDGE_SEEDS``: what a tool call fills itself beyond the kernel's set."""

from __future__ import annotations

from django_service_specs import RESERVED_POOL_SEEDS
from rest_framework_services.dispatch.utils import DROPPED_EDGE_SEEDS, EDGE_SEEDS

from django_spec_mcp.types.tool_edge_seeds import TOOL_EDGE_SEEDS


def test_the_tool_edge_seeds_are_what_the_drf_edge_fills() -> None:
    # Written out in ``types/tool_edge_seeds.py``; pinned to drf-services' own sets so an
    # upstream change to what the edge seeds or drops fails here.
    assert frozenset(EDGE_SEEDS) | DROPPED_EDGE_SEEDS == TOOL_EDGE_SEEDS
    assert not (TOOL_EDGE_SEEDS & RESERVED_POOL_SEEDS)
