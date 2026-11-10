from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured

from django_spec_mcp.auth.principal_for_token import principal_for_token
from django_spec_mcp.auth.types.token_info import TokenInfo

# --- an authenticated caller with no primary key -----------------------------


class _Nameless:
    """A resolved principal a backend might plausibly return: no ``pk``."""

    is_authenticated = True


class _Undeclared:
    """Declares neither ``pk`` nor ``is_authenticated`` — the ambiguous case."""


def test_an_authenticated_caller_with_no_pk_is_refused() -> None:
    """The security content of this function."""
    with pytest.raises(ImproperlyConfigured) as excinfo:
        principal_for_token(TokenInfo(user=_Nameless(), scopes=()))

    message = str(excinfo.value)
    assert "_Nameless" in message
    assert "pk" in message
    assert "AnonymousUser" in message


def test_a_user_declaring_neither_is_refused_too() -> None:
    """Ambiguity resolves to refusal, not to sharing."""
    with pytest.raises(ImproperlyConfigured):
        principal_for_token(TokenInfo(user=_Undeclared(), scopes=()))


def test_a_deliberately_anonymous_caller_still_shares_the_principal() -> None:
    """Sharing is correct when nobody was identified."""
    assert principal_for_token(TokenInfo(user=AnonymousUser(), scopes=())) == "anonymous"


def test_a_token_with_no_user_at_all_is_anonymous() -> None:
    assert principal_for_token(TokenInfo(user=None, scopes=())) == "anonymous"
