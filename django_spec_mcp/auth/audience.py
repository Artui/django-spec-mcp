from __future__ import annotations

from collections.abc import Sequence


def audience_matches(token_audience: str | Sequence[str] | None, expected: str | None) -> bool:
    """Return True if the token's audience satisfies the configured resource URL.

    RFC 8707 audience binding. ``expected is None`` disables enforcement (the
    binding happens upstream, e.g. at a gateway). Otherwise the comparison is
    exact — token audiences are URLs, not patterns — and a token carrying no
    audience is rejected rather than accepted, which is the whole point of
    configuring the canonical URL.

    A token may name several resources: a JWT ``aud`` may be an array (RFC 7519
    4.1.3), and django-oauth-toolkit stores ``AccessToken.resource`` as a list.
    A sequence matches when ``expected`` is one of its members, each compared
    exactly, and an empty one matches nothing. A ``str`` is checked first
    because it is itself a ``Sequence[str]``, and membership in a string is a
    substring test (``test_no_partial_match_for_safety``). The members are
    compared one by one rather than with ``in``, since ``in`` is a substring test
    on a ``UserString`` and raises ``TypeError`` on ``bytes``, which would turn a
    401 into a 500 (``test_a_user_string_member_is_no_substring_match``,
    ``test_a_bytes_audience_is_rejected_not_raised``).
    """
    if expected is None:
        return True
    if token_audience is None:
        return False
    if isinstance(token_audience, str):
        return token_audience == expected
    return any(member == expected for member in token_audience)


__all__ = ["audience_matches"]
