from __future__ import annotations

from collections.abc import Sequence

import pytest

from django_spec_mcp.auth.audience import audience_matches


def test_unset_expected_disables_enforcement() -> None:
    """When no expected resource URL is given, audience is not enforced."""
    assert audience_matches(None, None) is True
    assert audience_matches("https://anything", None) is True


def test_token_without_audience_rejected_when_expected_set() -> None:
    """A token with no ``aud`` claim cannot satisfy a configured resource."""
    assert audience_matches(None, "https://example.com/mcp/") is False


def test_exact_match() -> None:
    assert audience_matches("https://example.com/mcp/", "https://example.com/mcp/") is True


def test_mismatch_rejected() -> None:
    assert audience_matches("https://other.example/mcp/", "https://example.com/mcp/") is False


def test_no_partial_match_for_safety() -> None:
    """Token audiences are URLs, not patterns — substring matches must not pass.

    It also holds the order of the checks: a ``str`` is itself a
    ``Sequence[str]``, so a string read as a sequence would make the second
    case a substring test, and it would pass.
    """
    assert audience_matches("https://example.com/mcp", "https://example.com/mcp/") is False
    assert audience_matches("https://example.com/mcp/x", "https://example.com/mcp/") is False


_HERE = "https://example.com/mcp/"


@pytest.mark.parametrize(
    "audience",
    [[_HERE], ["https://other.example/mcp/", _HERE], ("https://other.example/mcp/", _HERE)],
    ids=["only-member", "list", "tuple"],
)
def test_a_token_naming_several_resources_matches_when_this_server_is_one(
    audience: Sequence[str],
) -> None:
    """A JWT ``aud`` array and django-oauth-toolkit's ``resource`` list are sequences."""
    assert audience_matches(audience, _HERE) is True


def test_a_token_whose_resources_leave_this_server_out_is_refused() -> None:
    assert audience_matches(["https://other.example/mcp/"], _HERE) is False


def test_a_member_is_compared_exactly_too() -> None:
    """Membership is equality per member, so a prefix member does not pass."""
    assert audience_matches(["https://example.com/", "https://example.com/mcp"], _HERE) is False


@pytest.mark.parametrize("audience", [[], ()], ids=["list", "tuple"])
def test_a_token_naming_no_resource_matches_nothing(audience: Sequence[str]) -> None:
    """django-oauth-toolkit stores an empty list when the client sent no ``resource``."""
    assert audience_matches(audience, _HERE) is False


def test_unset_expected_disables_enforcement_for_a_sequence_too() -> None:
    assert audience_matches(["https://anything"], None) is True
    assert audience_matches([], None) is True
