from __future__ import annotations

import pytest


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def is_async(request: pytest.FixtureRequest) -> bool:
    """The sync view and handler, then the async one, for a test asking both."""
    return request.param
