from __future__ import annotations

import pytest
from django.urls import clear_url_caches

pytestmark = pytest.mark.urls("tests.conformance.urls")


@pytest.mark.django_db
def test_a_session_minted_before_the_url_caches_clear_is_unknown_after(
    jsonrpc, initialized_session: str
) -> None:
    # What pytest-django does between two tests marked with the same conf: it
    clear_url_caches()

    response = jsonrpc("tools/list", {}, session_id=initialized_session)

    assert response.status_code == 404, response.content
