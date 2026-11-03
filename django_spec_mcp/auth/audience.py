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
    substring test (``test_no_partial_match_for_safety``).
    """
    if expected is None:
        return True
    if token_audience is None:
        return False
    if isinstance(token_audience, str):
        return token_audience == expected
    return expected in token_audience


__all__ = ["audience_matches"]
