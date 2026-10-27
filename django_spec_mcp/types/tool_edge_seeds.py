"""``TOOL_EDGE_SEEDS`` - names a tool call fills itself, beyond the kernel's reserved set, that no channel may take."""

from __future__ import annotations

TOOL_EDGE_SEEDS: frozenset[str] = frozenset({"request", "view", "serializer"})
"""Names a tool call fills itself, beyond the kernel's reserved set, that no channel may take.

drf-services' DRF edge seeds ``request`` and ``view`` on every ``dispatch_spec``
call (its ``EDGE_SEEDS``), and its validator binds ``serializer``; a caller's
key of the first or third name is dropped (its ``DROPPED_EDGE_SEEDS``). None of
the three is in the kernel's ``RESERVED_POOL_SEEDS``, which
``validate_channel_names`` always reserves, so a ``UrlKwarg`` or ``QueryParam``
of one of these names registered, was listed, and never reached what read it.
``validate_url_kwargs`` and ``validate_query_params`` reserve these beside the
post-fetch keys and the server's ``pool_seeds``
(``test_a_url_kwarg_named_after_an_edge_seed_is_refused``,
``test_a_query_param_named_after_an_edge_seed_is_refused``), which is the rule
drf-services applies to a spec's own ``reads`` when it compiles one.

Written out rather than composed from drf-services' internal module, and pinned
equal to ``DROPPED_EDGE_SEEDS | EDGE_SEEDS`` by
``test_the_tool_edge_seeds_are_what_the_drf_edge_fills``, so an upstream change
fails a test here instead of moving a public constant without a word. A
resource read seeds only ``request`` (``RESOURCE_EDGE_SEEDS``), because it runs
its selector directly, with no view and no validator.
"""
