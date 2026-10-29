"""The fixed window's own questions; ``test_rate_limit_contract.py`` asks the shared ones."""

from __future__ import annotations

import pytest

from django_spec_mcp.auth.rate_limits.fixed_window_rate_limit import FixedWindowRateLimit


def test_invalid_max_calls_rejected() -> None:
    with pytest.raises(ValueError, match="max_calls"):
        FixedWindowRateLimit(max_calls=0, per_seconds=60)


def test_invalid_per_seconds_rejected() -> None:
    with pytest.raises(ValueError, match="per_seconds"):
        FixedWindowRateLimit(max_calls=1, per_seconds=0)
