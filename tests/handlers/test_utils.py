"""Unit coverage for the ``handlers.utils`` helpers both call paths share.

``same_arguments`` judges whether an answer moved the route a call names, by
value and by type; the kernel and drf-services paths ask it alike, and it reads
no DRF, so it is asked here as it was.

The drf-services original also asked ``validate_input_against_serializer``, the
read path's DRF serializer validator, and ``split_url_kwargs`` /
``split_query_params``, which route ``UrlKwarg`` and ``QueryParam`` values. None
has a kernel counterpart: a kernel spec validates through its own ``Validator``
inside ``dispatch``, and its arguments are its ``reads`` and ``shapes``, with no
route or query string to split them onto. It is kept beside this file as
``test_utils_drfs.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from django_spec_mcp.handlers.utils import same_arguments

_AN_OBJECT = object()


class _Equal:
    """Equal to every other instance, with the default ``repr`` naming its address."""

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Equal)

    def __hash__(self) -> int:
        return 0


@pytest.mark.parametrize(
    ("answered", "delivered", "same"),
    [
        ({"pk": 1}, {"pk": 1}, True),
        ({}, {}, True),
        # The same names in another order are the same route.
        ({"a": 1, "b": "x"}, {"b": "x", "a": 1}, True),
        # One object is one value, whatever its ``repr``.
        ({"pk": _AN_OBJECT}, {"pk": _AN_OBJECT}, True),
        # Equal under ``==``, each naming another row through a ``CharField``.
        ({"pk": True}, {"pk": 1}, False),
        ({"pk": 1.0}, {"pk": 1}, False),
        ({"pk": -0.0}, {"pk": 0.0}, False),
        ({"pk": "1"}, {"pk": 1}, False),
        # Nested values are compared the same way.
        ({"pk": [True]}, {"pk": [1]}, False),
        ({"pk": {"id": 1.0}}, {"pk": {"id": 1}}, False),
        # Erring toward judging again: a nested mapping in another order, and
        # two equal objects whose ``repr`` is their address.
        ({"pk": {"a": 1, "b": 2}}, {"pk": {"b": 2, "a": 1}}, False),
        ({"pk": _Equal()}, {"pk": _Equal()}, False),
        # A name filled, or cleared, is a move.
        ({"pk": 1}, {}, False),
        ({}, {"pk": 1}, False),
    ],
    ids=[
        "same",
        "both-empty",
        "names-reordered",
        "same-object",
        "true-for-1",
        "float-for-int",
        "negative-zero",
        "string-for-int",
        "nested-list",
        "nested-mapping",
        "nested-mapping-reordered",
        "equal-objects",
        "filled",
        "cleared",
    ],
)
def test_same_arguments_is_the_same_value_of_the_same_type(
    answered: dict[str, Any], delivered: dict[str, Any], same: bool
) -> None:
    assert same_arguments(answered, delivered) is same
